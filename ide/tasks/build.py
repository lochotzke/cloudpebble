import os
import shutil
import subprocess
import tempfile
import traceback
import zipfile
import json
import resource

from celery import task

from django.conf import settings
from django.utils.timezone import now

import apptools.addr2lines
from ide.utils.sdk import generate_wscript_file, generate_jshint_file, generate_manifest_dict, \
    generate_simplyjs_manifest_dict, generate_pebblejs_manifest_dict
from utils.keen_helper import send_keen_event

from ide.models.build import BuildResult, BuildSize
from ide.models.files import SourceFile, ResourceFile, ResourceVariant
from ide.utils.prepreprocessor import process_file as check_preprocessor_directives

__author__ = 'katharine'


def _set_resource_limits():
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20)) # 20 seconds of CPU time
    resource.setrlimit(resource.RLIMIT_NOFILE, (100, 100)) # 100 open files
    resource.setrlimit(resource.RLIMIT_RSS, (20 * 1024 * 1024, 20 * 1024 * 1024)) # 20 MB of memory
    resource.setrlimit(resource.RLIMIT_FSIZE, (5 * 1024 * 1024, 5 * 1024 * 1024)) # 5 MB output files.


def create_source_files(project, base_dir):
    """
    :param project: Project
    """
    source_files = project.source_files.all()
    src_dir = os.path.join(base_dir, 'src')
    if project.project_type == 'pebblejs':
        src_dir = os.path.join(src_dir, 'js')
    worker_dir = None
    try:
        os.mkdir(src_dir)
    except OSError as e:
        if e.errno == 17:  # file exists
            pass
        else:
            raise
    for f in source_files:
        target_dir = src_dir
        if f.target == 'worker' and project.project_type == 'native':
            if worker_dir is None:
                worker_dir = os.path.join(base_dir, 'worker_src')
                os.mkdir(worker_dir)
            target_dir = worker_dir

        abs_target = os.path.abspath(os.path.join(target_dir, f.file_name))
        if not abs_target.startswith(target_dir):
            raise Exception("Suspicious filename: %s" % f.file_name)
        abs_target_dir = os.path.dirname(abs_target)
        if not os.path.exists(abs_target_dir):
            os.makedirs(abs_target_dir)
        f.copy_to_path(abs_target)
        # Make sure we don't duplicate downloading effort; just open the one we created.
        with open(abs_target) as fh:
            check_preprocessor_directives(abs_target_dir, abs_target, fh.read())


def save_debug_info(base_dir, build_result, kind, platform, elf_file):
    path = os.path.join(base_dir, 'build', elf_file)
    if os.path.exists(path):
        try:
            debug_info = apptools.addr2lines.create_coalesced_group(path)
        except:
            print traceback.format_exc()
        else:
            build_result.save_debug_info(debug_info, platform, kind)


def store_size_info(project, build_result, platform, zip_file):
    platform_dir = platform + '/'
    if platform == 'aplite' and project.sdk_version == '2':
        platform_dir = ''
    try:
        build_size = BuildSize.objects.create(
            build=build_result,
            binary_size=zip_file.getinfo(platform_dir + 'pebble-app.bin').file_size,
            resource_size=zip_file.getinfo(platform_dir + 'app_resources.pbpack').file_size,
            platform=platform,
        )
        try:
            build_size.worker_size = zip_file.getinfo(platform_dir + 'pebble-worker.bin').file_size
        except KeyError:
            pass
        build_size.save()
    except KeyError:
        pass


@task(ignore_result=True, acks_late=True)
def run_compile(build_result):
    build_result = BuildResult.objects.get(pk=build_result)
    project = build_result.project
    source_files = SourceFile.objects.filter(project=project)
    resources = ResourceFile.objects.filter(project=project)

    # Assemble the project somewhere
    base_dir = tempfile.mkdtemp(dir=os.path.join(settings.CHROOT_ROOT, 'tmp') if settings.CHROOT_ROOT else None)

    try:
        # Resources
        resource_root = 'resources'
        os.makedirs(os.path.join(base_dir, resource_root, 'images'))
        os.makedirs(os.path.join(base_dir, resource_root, 'fonts'))
        os.makedirs(os.path.join(base_dir, resource_root, 'data'))

        if project.project_type == 'native':
            # Source code
            create_source_files(project, base_dir)

            manifest_dict = generate_manifest_dict(project, resources)
            open(os.path.join(base_dir, 'appinfo.json'), 'w').write(json.dumps(manifest_dict))

            for f in resources:
                target_dir = os.path.abspath(os.path.join(base_dir, resource_root, ResourceFile.DIR_MAP[f.kind]))
                abs_target = os.path.abspath(os.path.join(target_dir, f.file_name))
                f.copy_all_variants_to_dir(target_dir)

            # Reconstitute the SDK
            open(os.path.join(base_dir, 'wscript'), 'w').write(generate_wscript_file(project))
            open(os.path.join(base_dir, 'pebble-jshintrc'), 'w').write(generate_jshint_file(project))
        elif project.project_type == 'simplyjs':
            shutil.rmtree(base_dir)
            shutil.copytree(settings.SIMPLYJS_ROOT, base_dir)
            manifest_dict = generate_simplyjs_manifest_dict(project)

            js = '\n\n'.join(x.get_contents() for x in source_files if x.file_name.endswith('.js'))
            escaped_js = json.dumps(js)
            build_result.save_simplyjs(js)

            open(os.path.join(base_dir, 'appinfo.json'), 'w').write(json.dumps(manifest_dict))
            open(os.path.join(base_dir, 'src', 'js', 'zzz_userscript.js'), 'w').write("""
            (function() {
                simply.mainScriptSource = %s;
            })();
            """ % escaped_js)
        elif project.project_type == 'pebblejs':
            shutil.rmtree(base_dir)
            shutil.copytree(settings.PEBBLEJS_ROOT, base_dir)
            manifest_dict = generate_pebblejs_manifest_dict(project, resources)
            create_source_files(project, base_dir)

            for f in resources:
                if f.kind != 'png':
                    continue
                target_dir = os.path.abspath(os.path.join(base_dir, resource_root, ResourceFile.DIR_MAP[f.kind]))
                abs_target = os.path.abspath(os.path.join(target_dir, f.file_name))
                if not abs_target.startswith(target_dir):
                    raise Exception("Suspicious filename: %s" % f.file_name)
                f.get_default_variant().copy_to_path(abs_target)

            open(os.path.join(base_dir, 'appinfo.json'), 'w').write(json.dumps(manifest_dict))

        # Build the thing
        cwd = os.getcwd()
        success = False
        output = 'Failed to get output'
        build_start_time = now()
        try:
            os.chdir(base_dir)
            if project.sdk_version == '2':
                environ = os.environ
                command = [settings.SDK2_PEBBLE_TOOL, "build"]
            elif project.sdk_version == '3':
                environ = os.environ.copy()
                environ['PATH'] = '{}:{}'.format(settings.ARM_CS_TOOLS, environ['PATH'])
                command = [settings.SDK3_PEBBLE_WAF, "configure", "build"]
            else:
                raise Exception("invalid sdk version.")
            output = subprocess.check_output(command, stderr=subprocess.STDOUT, preexec_fn=_set_resource_limits,
                                             env=environ)
        except subprocess.CalledProcessError as e:
            output = e.output
            print output
            success = False
        except Exception as e:
            success = False
            output = str(e)
        else:
            success = True
            temp_file = os.path.join(base_dir, 'build', '%s.pbw' % os.path.basename(base_dir))
            if not os.path.exists(temp_file):
                success = False
                print "Success was a lie."
        finally:
            build_end_time = now()
            os.chdir(cwd)

            if success:
                # Try reading file sizes out of it first.
                try:
                    s = os.stat(temp_file)
                    build_result.total_size = s.st_size
                    # Now peek into the zip to see the component parts
                    with zipfile.ZipFile(temp_file, 'r') as z:
                        store_size_info(project, build_result, 'aplite', z)
                        store_size_info(project, build_result, 'basalt', z)
                        store_size_info(project, build_result, 'chalk', z)

                except Exception as e:
                    print "Couldn't extract filesizes: %s" % e

                # Try pulling out debug information.
                if project.sdk_version == '2':
                    save_debug_info(base_dir, build_result, BuildResult.DEBUG_APP, 'aplite', os.path.join(base_dir, 'build', 'pebble-app.elf'))
                    save_debug_info(base_dir, build_result, BuildResult.DEBUG_WORKER, 'aplite', os.path.join(base_dir, 'build', 'pebble-worker.elf'))
                else:
                    save_debug_info(base_dir, build_result, BuildResult.DEBUG_APP, 'aplite', os.path.join(base_dir, 'build', 'aplite/pebble-app.elf'))
                    save_debug_info(base_dir, build_result, BuildResult.DEBUG_WORKER, 'aplite', os.path.join(base_dir, 'build', 'aplite/pebble-worker.elf'))
                    save_debug_info(base_dir, build_result, BuildResult.DEBUG_APP, 'basalt', os.path.join(base_dir, 'build', 'basalt/pebble-app.elf'))
                    save_debug_info(base_dir, build_result, BuildResult.DEBUG_WORKER, 'basalt', os.path.join(base_dir, 'build', 'basalt/pebble-worker.elf'))
                    save_debug_info(base_dir, build_result, BuildResult.DEBUG_APP, 'chalk', os.path.join(base_dir, 'build', 'chalk/pebble-app.elf'))
                    save_debug_info(base_dir, build_result, BuildResult.DEBUG_WORKER, 'chalk', os.path.join(base_dir, 'build', 'chalk/pebble-worker.elf'))


                build_result.save_pbw(temp_file)
            build_result.save_build_log(output)
            build_result.state = BuildResult.STATE_SUCCEEDED if success else BuildResult.STATE_FAILED
            build_result.finished = now()
            build_result.save()

            data = {
                'data': {
                    'cloudpebble': {
                        'build_id': build_result.id,
                        'job_run_time': (build_result.finished - build_result.started).total_seconds(),
                    },
                    'build_time': (build_end_time - build_start_time).total_seconds(),
                }
            }

            event_name = 'app_build_succeeded' if success else 'app_build_failed'

            send_keen_event(['cloudpebble', 'sdk'], event_name, data, project=project)

    except Exception as e:
        print "Build failed due to internal error: %s" % e
        traceback.print_exc()
        build_result.state = BuildResult.STATE_FAILED
        build_result.finished = now()
        try:
            build_result.save_build_log("Something broke:\n%s" % e)
        except:
            pass
        build_result.save()
    finally:
        # shutil.rmtree(base_dir)
        print base_dir