import datetime
import time
import json
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST, require_safe
from django.utils.translation import ugettext as _
from ide.api import json_failure, json_response
from ide.models.project import Project
from ide.models.files import SourceFile
from utils.td_helper import send_td_event

__author__ = 'katharine'


@require_POST
@login_required
def create_source_file(request, project_id):
    project = get_object_or_404(Project, pk=project_id, owner=request.user)
    try:
        f = SourceFile.objects.create(project=project,
                                      file_name=request.POST['name'],
                                      target=request.POST.get('target', 'app'))
        f.save_file(request.POST.get('content', ''))
    except IntegrityError as e:
        return json_failure(str(e))
    else:
        send_td_event('cloudpebble_create_file', data={
            'data': {
                'filename': request.POST['name'],
                'kind': 'source',
                'target': f.target,
            }
        }, request=request, project=project)

        return json_response({"file": {"id": f.id, "name": f.file_name, "target": f.target}})


@require_safe
@csrf_protect
@login_required
def load_source_file(request, project_id, file_id):
    project = get_object_or_404(Project, pk=project_id, owner=request.user)
    source_file = get_object_or_404(SourceFile, pk=file_id, project=project)
    try:
        content = source_file.get_contents()

        try:
            folded_lines = json.loads(source_file.folded_lines)
        except ValueError:
            folded_lines = []

        send_td_event('cloudpebble_open_file', data={
            'data': {
                'filename': source_file.file_name,
                'kind': 'source'
            }
        }, request=request, project=project)

    except Exception as e:
        return json_failure(str(e))
    else:
        return json_response({
            "success": True,
            "source": content,
            "modified": time.mktime(source_file.last_modified.utctimetuple()),
            "folded_lines": folded_lines
        })


@require_safe
@csrf_protect
@login_required
def source_file_is_safe(request, project_id, file_id):
    project = get_object_or_404(Project, pk=project_id, owner=request.user)
    source_file = get_object_or_404(SourceFile, pk=file_id, project=project)
    client_modified = datetime.datetime.fromtimestamp(int(request.GET['modified']))
    server_modified = source_file.last_modified.replace(tzinfo=None, microsecond=0)
    is_safe = client_modified >= server_modified
    return json_response({'safe': is_safe})


@require_POST
@login_required
def rename_source_file(request, project_id, file_id):
    project = get_object_or_404(Project, pk=project_id, owner=request.user)
    source_file = get_object_or_404(SourceFile, pk=file_id, project=project)
    old_filename = source_file.file_name
    try:
        if source_file.file_name != request.POST['old_name']:
            send_td_event('cloudpebble_rename_abort_unsafe', data={
                'data': {
                    'filename': source_file.file_name,
                    'kind': 'source'
                }
            }, request=request, project=project)
            raise Exception(_("Could not rename, file has been renamed already."))
        if source_file.was_modified_since(int(request.POST['modified'])):
            send_td_event('cloudpebble_rename_abort_unsafe', data={
                'data': {
                    'filename': source_file.file_name,
                    'kind': 'source',
                    'modified': time.mktime(source_file.last_modified.utctimetuple()),
                }
            }, request=request, project=project)
            raise Exception(_("Could not rename, file has been modified since last save."))
        source_file.file_name = request.POST['new_name']
        source_file.save()

    except Exception as e:
        return json_failure(str(e))
    else:
        send_td_event('cloudpebble_rename_file', data={
            'data': {
                'old_filename': old_filename,
                'new_filename': source_file.file_name,
                'kind': 'source'
            }
        }, request=request, project=project)
        return json_response({"modified": time.mktime(source_file.last_modified.utctimetuple())})


@require_POST
@login_required
def save_source_file(request, project_id, file_id):
    project = get_object_or_404(Project, pk=project_id, owner=request.user)
    source_file = get_object_or_404(SourceFile, pk=file_id, project=project)
    try:
        if source_file.was_modified_since(int(request.POST['modified'])):
            send_td_event('cloudpebble_save_abort_unsafe', data={
                'data': {
                    'filename': source_file.file_name,
                    'kind': 'source'
                }
            }, request=request, project=project)
            raise Exception(_("Could not save: file has been modified since last save."))
        source_file.save_file(request.POST['content'], folded_lines=request.POST['folded_lines'])

    except Exception as e:
        return json_failure(str(e))
    else:
        send_td_event('cloudpebble_save_file', data={
            'data': {
                'filename': source_file.file_name,
                'kind': 'source'
            }
        }, request=request, project=project)

        return json_response({"modified": time.mktime(source_file.last_modified.utctimetuple())})


@require_POST
@login_required
def delete_source_file(request, project_id, file_id):
    project = get_object_or_404(Project, pk=project_id, owner=request.user)
    source_file = get_object_or_404(SourceFile, pk=file_id, project=project)
    try:
        source_file.delete()
    except Exception as e:
        return json_failure(str(e))
    else:
        send_td_event('cloudpebble_delete_file', data={
            'data': {
                'filename': source_file.file_name,
                'kind': 'source'
            }
        }, request=request, project=project)
        return json_response({})
