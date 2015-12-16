$(function() {
    $('#create-project').click(function() {
        $('#create-project').find('input button select').removeAttr('disabled');
        $('#project-prompt').modal();
    });
    $('#project-type').change(function() {
        var val = $(this).val();
        if(val != 'native') {
            $('#project-template').val(0);
            $('#template-holder').hide();
            if (val == 'pebblejs') {
                $('#project-sdk-version').val('3');
            } else {
                $('#project-sdk-version').val('2');
            }
            $('.sdk-version').hide();
        } else {
            $('#template-holder').show();
            $('.sdk-version').show();
        }
    });
    $('#project-sdk-version').change(function() {
        var val = $(this).val();
        if(val == '3') {
            $('#project-type').find('[value=simplyjs]').attr('disabled', 'disabled');
            $('#project-type').find('[value=pebblejs]').removeAttr('disabled');
        } else {
            $('#project-type').find('[value=simplyjs]').removeAttr('disabled');
            $('#project-type').find('[value=pebblejs]').attr('disabled', 'disabled');
        }
    });

    $('#project-confirm-button').click(function() {
        var value = $('#project-prompt-value').val();
        $('project-prompt-errors').addClass("hide");
        if(value === '') {
            $('#project-prompt-errors').removeClass('hide').text(gettext("You must enter a name."));
            return;
        }
        $('#create-project').find('input button select').attr('disabled', 'disabled');
        $.post('/ide/project/create', {
                name: value,
                template: $('#project-template').val(),
                type: $('#project-type').val(),
                sdk: $('#project-sdk-version').val()
            }, function(data) {
                if(!data.success) {
                    $('#project-prompt-errors').removeClass('hide').text(data.error);
                } else {
                    window.location.href = "/ide/project/" + data.id;
                }
            }
        );
    });

    var disable_import_controls = function() {
        $('#import-prompt').find('input, button').attr('disabled', 'disabled');
        $('#import-prompt').find('.nav > li').addClass('disabled').find('a').removeAttr('data-toggle');
    };

    var enable_import_controls = function() {
        $('#import-prompt').find('input, button').removeAttr('disabled');
        $('#import-prompt').find('.nav > li').removeClass('disabled').find('a').attr('data-toggle', 'tab');
    };

    var handle_import_progress = function(active_set, task_id, project_id) {
        var check = function() {
            $.getJSON('/ide/task/' + task_id, function(data) {
                if(data.state.status == 'SUCCESS') {
                    window.location.href = '/ide/project/' + project_id;
                    return;
                } else if(data.state.status == 'FAILURE') {
                    active_set.find('.errors').removeClass('hide').text(interpolate(gettext("Error: %s"), [data.state.result]));
                    enable_import_controls();
                    active_set.find('.progress').addClass('hide');
                    return;
                } else {
                    setTimeout(check, 1000);
                }
            });
        };
        setTimeout(check, 1000);
    };

    var import_archive = function(active_set) {
        var name = active_set.find('#import-zip-name').val();
        if(name.replace(/\s/g, '') === '') {
            active_set.find('.errors').removeClass('hide').text(gettext("You must specify a project name."));
            return;
        }
        var files = active_set.find('input[type=file]').get(0).files;
        if(files.length != 1) {
            active_set.find('.errors').removeClass('hide').text(gettext("You must upload a zip file."));
            return;
        }
        var file = files[0];
        // This check seems to fail on some systems.
        //if(file.type != 'application/zip' && file.type != 'application/x-zip-compressed') {
        //    active_set.find('.errors').removeClass('hide').text("You must upload a zip file.");
        //    return;
        //}
        disable_import_controls();
        var form_data = new FormData();
        form_data.append('name', name);
        form_data.append('archive', file);
        active_set.find('.progress').removeClass('hide');

        $.ajax({
            url: '/ide/import/zip',
            type: "POST",
            data: form_data,
            processData: false,
            contentType: false,
            dataType: 'json',
            success: function(data) {
                if(data.success) {
                    handle_import_progress(active_set, data.task_id, data.project_id);
                } else {
                    active_set.find('.errors').removeClass('hide').text(data.error);
                    enable_import_controls();
                    active_set.find('.progress').addClass('hide');
                }
            }
        });
        ga('send', 'event', 'project', 'import', 'zip');
    };

    var import_github = function(active_set) {
        var name = active_set.find('#import-github-name').val();
        var url = active_set.find('#import-github-url').val();
        var branch = active_set.find('#import-github-branch').val();
        var add_remote = !!active_set.find('#import-github-add-remote').is(':checked');
        if(name.replace(/\s/g, '') === '') {
            active_set.find('.errors').removeClass('hide').text(gettext("You must specify a project name."));
            return;
        }
        // This is identical to the regex used on the server.
        if(!/^(?:https?:\/\/|git@|git:\/\/)?(?:www\.)?github\.com[\/:]([\w.-]+)\/([\w.-]+?)(?:\.git|\/|$)/.test(url)) {
            active_set.find('.errors').removeClass('hide').text(gettext("You must specify a complete GitHub project URL"));
            return;
        }
        if(branch.length == 0) {
            branch = 'master';
        }
        disable_import_controls();
        active_set.find('.progress').removeClass('hide');
        $.post('/ide/import/github', {name: name, repo: url, branch: branch, add_remote: add_remote}, function(data) {
            if(data.success) {
                handle_import_progress(active_set, data.task_id, data.project_id);
            } else {
                active_set.find('.errors').removeClass('hide').text(data.error);
                enable_import_controls();
                active_set.find('.progress').addClass('hide');
            }
        });
        ga('send', 'event', 'project', 'import', 'github');
    };

    var run_project_import = function() {
        var active_set = $('#import-prompt .tab-pane.active');
        active_set.find('.errors').addClass('hide');
        if(active_set.attr('id') == 'import-zip') {
            import_archive(active_set);
        } else if(active_set.attr('id') == 'import-github') {
            import_github(active_set);
        }
    };

    $('#run-import').click(run_project_import);
    $('#import-prompt form').submit(function (e) {
        e.preventDefault();
        $('#run-import').click();
    });
    
    $('#import-project').click(function() {
        $('#import-prompt').modal();
    });

    $('#project-prompt form').submit(function (e){
        e.preventDefault();
        $('#project-confirm-button').click();
    });

    // Clean up stray forward slashes.
    var path = location.pathname.replace(/\/+/g, '/');
    if (path.indexOf('/ide/import/github/') === 0) {
        var parts = path.substr(1).split('/');
        $('#import-prompt').modal();
        $('#import-github-name').val(parts[3]);
        $('#import-github-url').val('github.com/' + parts[3] + '/' + parts[4]);
        if (parts.length > 5) {
            $('#import-github-branch').val(parts.slice(5).join('/'));
        }
        $('a[href=#import-github]').tab('show');
    }

    jquery_csrf_setup();
});
