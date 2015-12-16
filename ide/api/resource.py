import json
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST, require_safe
from ide.api import json_failure, json_response
from ide.models.project import Project
from ide.models.files import ResourceFile, ResourceIdentifier, ResourceVariant
from utils.keen_helper import send_keen_event
import utils.s3 as s3

__author__ = 'katharine'


def decode_resource_id_options(request):
    """ Extract resource ID options from a HTTP request, making sure the keys have the same names as the
    ResourceIdentifier object's fields. """
    return {
        # Resource ID
        'resource_id': request['id'],
        'target_platforms': json.dumps(request['target_platforms']) if 'target_platforms' in request else None,

        # Font options
        'character_regex': request.get('regex', None),
        'tracking': int(request['tracking']) if 'tracking' in request else None,
        'compatibility': request.get('compatibility', None),

        # Bitmap options
        'memory_format': request.get('memory_format', None),
        'storage_format': request.get('storage_format', None),
        'space_optimisation': request.get('space_optimisation', None),
    }


@require_POST
@login_required
def create_resource(request, project_id):
    project = get_object_or_404(Project, pk=project_id, owner=request.user)
    kind = request.POST['kind']
    resource_ids = json.loads(request.POST['resource_ids'])
    posted_file = request.FILES.get('file', None)
    file_name = request.POST['file_name']
    new_tags = json.loads(request.POST['new_tags'])
    resources = []
    try:
        with transaction.atomic():
            rf = ResourceFile.objects.create(project=project, file_name=file_name, kind=kind)
            for r in resource_ids:
                resource_options = decode_resource_id_options(r)
                resources.append(ResourceIdentifier.objects.create(resource_file=rf, **resource_options))
            if posted_file is not None:
                variant = ResourceVariant.objects.create(resource_file=rf, tags=",".join(str(int(t)) for t in new_tags))
                variant.save_file(posted_file, posted_file.size)

            rf.save()

    except Exception as e:
        return json_failure(str(e))
    else:
        send_keen_event('cloudpebble', 'cloudpebble_create_file', data={
            'data': {
                'filename': file_name,
                'kind': 'resource',
                'resource-kind': kind
            }
        }, project=project, request=request)

        return json_response({"file": {
            "id": rf.id,
            "kind": rf.kind,
            "file_name": rf.file_name,
            "resource_ids": [x.get_options_dict(with_id=True) for x in resources],
            "identifiers": [x.resource_id for x in resources],
            "variants": [x.get_tags() for x in rf.variants.all()],
            "extra": {y.resource_id: y.get_options_dict(with_id=False) for y in rf.identifiers.all()}
        }})


@require_safe
@login_required
def resource_info(request, project_id, resource_id):
    project = get_object_or_404(Project, pk=project_id, owner=request.user)
    resource = get_object_or_404(ResourceFile, pk=resource_id)
    resources = resource.get_identifiers()

    send_keen_event('cloudpebble', 'cloudpebble_open_file', data={
        'data': {
            'filename': resource.file_name,
            'kind': 'resource',
            'resource-kind': resource.kind
        }
    }, project=project, request=request)

    return json_response({
        'resource': {
            'resource_ids': [x.get_options_dict(with_id=True) for x in resources],
            'id': resource.id,
            'file_name': resource.file_name,
            'kind': resource.kind,
            "variants": [x.get_tags() for x in resource.variants.all()],
            "extra": {y.resource_id: y.get_options_dict(with_id=False) for y in resource.identifiers.all()}
        }
    })


@require_POST
@login_required
def delete_resource(request, project_id, resource_id):
    project = get_object_or_404(Project, pk=project_id, owner=request.user)
    resource = get_object_or_404(ResourceFile, pk=resource_id, project=project)
    try:
        resource.delete()
    except Exception as e:
        return json_failure(str(e))
    else:
        send_keen_event('cloudpebble', 'cloudpebble_delete_file', data={
            'data': {
                'filename': resource.file_name,
                'kind': 'resource',
                'resource-kind': resource.kind
            }
        }, project=project, request=request)


        return json_response({})

@require_POST
@login_required
def delete_variant(request, project_id, resource_id, variant):
    project = get_object_or_404(Project, pk=project_id, owner=request.user)
    resource = get_object_or_404(ResourceFile, pk=resource_id, project=project)
    if variant == '0':
        variant = ''
    variant_to_delete = resource.variants.get(tags=variant)

    if resource.variants.count() == 1:
        return json_failure("You cannot delete the last remaining variant of a resource.")
    try:
        variant_to_delete.delete()
    except Exception as e:
        return json_failure(str(e))
    else:
        send_keen_event('cloudpebble', 'cloudpebble_delete_variant', data={
            'data': {
                'filename': resource.file_name,
                'kind': 'resource',
                'resource-kind': resource.kind,
                'variant': variant
            }
        }, project=project, request=request)

        return json_response({'resource': {
            'variants': [x.get_tags() for x in resource.variants.all()]
        }})


@require_POST
@login_required
def update_resource(request, project_id, resource_id):
    project = get_object_or_404(Project, pk=project_id, owner=request.user)
    resource = get_object_or_404(ResourceFile, pk=resource_id, project=project)
    resource_ids = json.loads(request.POST['resource_ids'])
    file_name = request.POST.get('file_name', None)
    variant_tags = json.loads(request.POST.get('variants', "[]"))
    new_tags = json.loads(request.POST.get('new_tags', "[]"))
    replacement_map = json.loads(request.POST.get('replacements', "[]"))
    replacement_files = request.FILES.getlist('replacement_files[]')
    try:
        with transaction.atomic():
            # Lazy approach: delete all the resource_ids and recreate them.
            # We could do better.
            resources = []
            ResourceIdentifier.objects.filter(resource_file=resource).delete()
            for r in resource_ids:
                resource_options = decode_resource_id_options(r)
                resources.append(ResourceIdentifier.objects.create(resource_file=resource, **resource_options))

            # We get sent a list of (tags_before, tags_after) pairs.
            updated_variants = []
            for tag_update in variant_tags:
                tags_before, tags_after = tag_update
                variant = resource.variants.get(tags=tags_before)
                variant.set_tags(tags_after)
                updated_variants.append(variant)

            for variant in updated_variants:
                variant.save()
            if 'file' in request.FILES:
                variant = resource.variants.create(tags=",".join(str(int(t)) for t in new_tags))
                variant.save_file(request.FILES['file'], request.FILES['file'].size)

            # We may get sent a list of pairs telling us which variant gets which replacement file
            for tags, file_index in replacement_map:
                variant = resource.variants.get(tags=tags)
                replacement = replacement_files[int(file_index)]
                variant.save_file(replacement, replacement.size)

            if file_name and resource.file_name != file_name:
                resource.file_name = file_name

            resource.save()

    except Exception as e:
        return json_failure(str(e))
    else:
        send_keen_event('cloudpebble', 'cloudpebble_save_file', data={
            'data': {
                'filename': resource.file_name,
                'kind': 'source'
            }
        }, project=project, request=request)

        return json_response({"file": {
            "id": resource.id,
            "kind": resource.kind,
            "file_name": resource.file_name,
            "resource_ids": [x.get_options_dict(with_id=True) for x in resources],
            "identifiers": [x.resource_id for x in resources],
            "variants": [x.get_tags() for x in resource.variants.all()],
            "extra": {y.resource_id: y.get_options_dict(with_id=False) for y in resource.identifiers.all()}
        }})


@require_safe
@login_required
def show_resource(request, project_id, resource_id, variant):
    resource = get_object_or_404(ResourceFile, pk=resource_id, project__owner=request.user)
    if variant == '0':
        variant = ''

    variant = resource.get_best_variant(variant)
    content_types = {
        u'png': 'image/png',
        u'png-trans': 'image/png',
        u'bitmap': 'image/png',
        u'font': 'application/octet-stream',
        u'raw': 'application/octet-stream'
    }
    content_disposition = "attachment; filename=\"%s\"" % resource.file_name
    content_type = content_types[resource.kind]
    if settings.AWS_ENABLED:
        headers = {
            'response-content-disposition': content_disposition,
            'Content-Type': content_type
        }
        return HttpResponseRedirect(s3.get_signed_url('source', variant.s3_path, headers=headers))
    else:
        response = HttpResponse(open(variant.local_filename), content_type=content_type)
        response['Content-Disposition'] = content_disposition
        return response
