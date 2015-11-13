CloudPebble.MonkeyScreenshots = (function() {
    var current_platforms = ['aplite', 'basalt', 'chalk'];
    var screenshot_editor_template;
    /** A simple class with default values for Screenshot files */
    function ScreenshotFile(options) {
        var final = _.defaults(options || {}, {
            is_new: false,
            id: null,
            file: null,
            src: "",
            _changed: false
        });
        this.is_new = final.is_new;
        this.id = final.id;
        this.file = final.file;
        this.src = final.src;
        this._changed = final._changed;
    }

    /** A simple class with default values for screenshot sets,
     * which makes ScreenshotFile instances of its children if it needs to.*/
    function ScreenshotSet(options) {
        var final = _.defaults(options || {}, {
            name: "",
            id: null,
            files: []
        });
        this.name = final.name;
        this.id = final.id;
        this.files = _.mapObject(final.files, function (file) {
            return ((file instanceof ScreenshotFile) ? file : new ScreenshotFile(file));
        });
    }

    /**
     * Put screenshot data in a format ready to be sent.
     * @param screenshots
     * @returns {{screenshots: Array, files: Array}}
     */
    var process_screenshots = function(screenshots) {
        var screenshots_data = [];
        var files = [];
        _.each(screenshots, function(screenshot) {
            var shot_data = {name: screenshot.name, files: {}};
            if (screenshot.id) {shot_data.id = screenshot.id;}
            _.each(screenshot.files, function(image, platform) {
                if (image.id || image.file) {
                    shot_data.files[platform] = {};
                    if (image.id) {shot_data.files[platform].id = image.id;}
                    if (image.file !== null) {
                        shot_data.files[platform].uploadId = files.length;
                        files.push(image.file);
                    }
                }
            }, this);
            if (_.keys(shot_data.files).length > 0)
                screenshots_data.push(shot_data);
        }, this);

        var form_data = new FormData();
        form_data.append('screenshots', JSON.stringify(screenshots_data));
        _.each(files, function(file) {
            form_data.append('files[]', file);
        });

        return form_data;
    };

    /** A mock API, for testing*/
    var MockAPI = function() {
        var screenshots = [{
            name: "Screenshot set 1",
            id: 0,
            files: {
                aplite: new ScreenshotFile({src: "/static/common/img/screenshot-aplite.png", id: 0}),
                basalt: new ScreenshotFile({src: "/static/common/img/screenshot-basalt.png", id: 1}),
                chalk:  new ScreenshotFile({src: "/static/common/img/screenshot-chalk.png",  id: 2})
            }
        }, {
            name: "Screenshot set 2",
            id: 1,
            files: {
                aplite: new ScreenshotFile({src: "/static/common/img/screenshot-aplite.png", id: 3}),
                basalt: new ScreenshotFile({src: "/static/common/img/screenshot-basalt.png", id: 4}),
                chalk:  new ScreenshotFile({src: "/static/common/img/screenshot-chalk.png",  id: 5})
            }
        }];

        /**
         * Get the current list of existing test screenshots
         * @param test_name name of test
         * @returns {jQuery.Deferred}
         */
        this.getScreenshots = function(test_name) {
            var defer = $.Deferred();
            setTimeout(function () {
                defer.resolve(_.map(screenshots, _.clone));
            }, 700);
            return defer.promise();
        };

        /**
         * Save the current state of the screenshots
         * @param test_name name of test
         * @param new_screenshots
         * @returns {*}
         */
        this.saveScreenshots = function(test_name, new_screenshots) {
            var defer = $.Deferred();
            var form_data = process_screenshots(new_screenshots);

            // Made the form data, now we just have to send it.

            setTimeout(function() {
                screenshots = _.map(new_screenshots, function(shot) {
                    var new_shot = _.clone(shot);
                    new_shot.files = _.mapObject(_.clone(new_shot.files), _.partial(_.extend, _, {is_new: false, file: null}));
                    new_shot._changed = false;
                    return new_shot;
                });
                defer.resolve();
            }, 700);
            return defer.promise();
        };
    };

    /** The actual API, for getting and saving screenshots with the Django backend */
    var AjaxAPI = function() {
        this.getScreenshots = function(test_id) {
            var url = "/ide/project/" + PROJECT_ID + "/test/" + test_id + "/screenshots/load";
            var defer = $.Deferred();
            $.ajax({
                url: url,
                dataType: 'json'
            }).done(function(result) {
                defer.resolve(_.map(result['screenshots'], function(screenshot_set) {
                    return new ScreenshotSet(screenshot_set);
                }));
            }).fail(function(err) {
                defer.reject(err);
            });
            return defer.promise();
        };

        this.saveScreenshots = function(test_id, new_screenshots) {
            var form_data = process_screenshots(new_screenshots);
            var url = "/ide/project/" + PROJECT_ID + "/test/" + test_id + "/screenshots/save";
            return $.ajax({
                url: url,
                type: "POST",
                data: form_data,
                processData: false,
                contentType: false,
                dataType: 'json'
            });

        }
    };

    var API = new AjaxAPI();

    /**
     * ScreenshotsModel manages a list of new screenshot files to be uploaded
     * @fires ScreenshotsModel.change when files are added or modified
     * @constructor
     */
    function ScreenshotsModel(test_name) {
        var self = this;
        var screenshots = [];
        var original_screenshots = [];
        var disabled = false;
        _.extend(this, Backbone.Events);

        /**
         * Update the list of screenshots to be uploaded with some new files. If multiple files are added at one index,
         * each file[i] is added to the screenshot[index+i]
         * @param files an Array of File objects
         * @param index the screenshot index to update, or null for new screenshots
         * @param platform a string naming the platform for all of the new screenshots
         */
        this.addUploadedFiles = function(files, index, platform) {
            if (disabled) return;
            var onloads = [];
            var loadFile = function(screenshotfile) {
                var reader = new FileReader();
                var defer = $.Deferred();
                reader.onload = function() {
                    screenshotfile.src = reader.result;
                    defer.resolve();
                };
                onloads.push(defer.promise());
                reader.readAsDataURL(screenshotfile.file);
            };

            if (index === null) {
                // Append all new screenshots, given them no name
                _.each(files, function(file) {
                    var upload = new ScreenshotSet({
                        _changed: true
                    });
                    upload.files[platform] = new ScreenshotFile({file: file, is_new: true});
                    screenshots.push(upload);
                    loadFile(upload.files[platform]);
                });
            }
            else {
                _.each(files, function(file, i) {
                    var upload = screenshots[index + i];
                    if (upload) {
                        // Update existing screenshots at the current index
                        var id = (upload.files[platform] ? upload.files[platform].id : null);
                        upload.files[platform] = new ScreenshotFile({file:file, id: id, is_new: true});
                        loadFile(upload.files[platform]);
                    }
                    else {
                        // If there was no screenshot to update, add the remaining files as new screenshots.
                        this.addUploadedFiles(files.slice(i), null, platform);
                    }
                }, this);
            }
            $.when.apply(this, onloads).then(function() {
                self.trigger('changed', screenshots);
            });
        };

        /**
         * ScreenshotsModel stores the currently uploaded screenshots
         * @constructor
         */
        this.loadScreenshots = function() {
            var timeout = setTimeout(function() {
                self.trigger('waiting');
            }, 500);
            API.getScreenshots(test_name).then(function(result) {
                screenshots = result;
                original_screenshots = _.map(result, _.clone);
                self.trigger('changed', result);
            }, function(error) {
                self.trigger('error', gettext("Error getting screenshots"));
                console.log(error);
            }).always(function() {
                clearTimeout(timeout);
            });
        };

        this.deleteFile = function(index, platform) {
            if (disabled) return;
            if (_.isObject(screenshots[index].files[platform])) {
                screenshots[index].files[platform] = {is_new: true};
                this.trigger('changed', screenshots);
            }
        };

        this.setName = function(index, name) {
            if (disabled) return;
            if (_.isString(name)) {
                var changed = (!(_.has(original_screenshots, index)) || (name != original_screenshots[index].name));
                screenshots[index].name = name;
                screenshots[index]._changed = changed;
                self.trigger('changed', screenshots);
            }
        };

        this.save = function() {
            if (disabled) return;
            disabled = true;
            var timeout = setTimeout(function() {
                self.trigger('waiting');
            }, 500);
            API.saveScreenshots(test_name, screenshots).then(function() {
                self.trigger('saved', true);
                self.loadScreenshots();
            }, function(jqXHR, textStatus, errorThrown) {
                self.trigger('error', {jqXHR: jqXHR, textStatus: textStatus, errorThrown: errorThrown, errorFor: gettext('save screenshots')});
            }).always(function() {
                disabled = false;
                clearTimeout(timeout);
            });
        };
    }

    /** This class keeps track of which platform is currently selected, and also
     * interacts with the SidePane */
    function UIState(pane) {
        var supported_platforms = ['aplite', 'basalt', 'chalk'];
        var single = false;
        _.extend(this, Backbone.Events);
        this.toggle = function(platform) {
            single = (single ? false : platform);
            var platforms = (single ? [single] : supported_platforms)
            this.trigger('changed', {
                platforms: platforms
            });
            // When the user clicks a platform title, this causes the SidePane to resize appropriately.
            $(pane).width(this.getSize());
            pane.trigger('resize', this.getSize());
        };

        this.initial = function() {
            return _.clone(supported_platforms);
        };

        this.getSize = function() {
            var platforms = (single ? [single] : supported_platforms)
            return (50+platforms.length*180)+"px";
        };
        // Set the initial size of the side pane.
        $(pane).width(this.getSize());
    }


    /**
     * This sets up a screenshot editor pane
     * @param test_name Name of test for this ScreenshotPane
     * @constructor
     */
    function ScreenshotPane(test_name) {
        var pane = $('<div>').toggleClass('monkey-pane');
        var uiState, screenshots, view;

        _.extend(this, Backbone.Events);
        // Set up the data/models and pass them to the UI.
        uiState = new UIState(pane);
        screenshots = new ScreenshotsModel(test_name);
        view = CloudPebble.MonkeyScreenshots.Interface(screenshots, uiState);
        view.render(pane.get()[0], {});

        /** Get the actual pane so it can be attached to an object */
        this.getPane = function() {
            return pane;
        };

        /** Destroy the contents of the pane */
        this.destroy = function() {
            pane.trigger('destroy');
            pane.empty();
            view = pane = screenshots = uiState = null;
        }
    }

    return {
        ScreenshotPane: ScreenshotPane
    }
})();
