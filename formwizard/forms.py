from django.utils.datastructures import SortedDict
from django.shortcuts import render_to_response
from django.template import RequestContext
from django.http import HttpResponseRedirect
from django.core.urlresolvers import reverse
from formwizard.storage import get_storage
from formwizard.storage.base import NoFileStorageException

from django.views.generic import View
from django.utils.decorators import classonlymethod

from django import forms
from django.forms import formsets

class FormWizard(View):
    """
    The basic FormWizard. This class needs a storage backend when creating
    an instance.
    """

    storage_name = None
    form_list = None
    initial_list = None
    instance_list = None
    condition_list = None

    @classonlymethod
    def as_view(cls, *args, **kwargs):
        return super(FormWizard, cls).as_view(
            **cls.build_init_kwargs(*args, **kwargs))

    @classmethod
    def build_init_kwargs(cls, storage_name, form_list, initial_list={},
        instance_list={}, condition_list={}):
        """
        Creates a dict with all needed parameters for the form wizard instances.
        `storage` is the storage backend, the place where step data and
        current state of the form gets saved.

        `form_list` is a list of forms. The list entries can be form classes
        of tuples of (`step_name`, `form_class`).

        `initial_list` contains a dictionary of initial data dictionaries.
        The key should be equal to the `step_name` in the `form_list`.

        `instance_list` contains a dictionary of instance objects. This list
        is only used when `ModelForms` are used. The key should be equal to
        the `step_name` in the `form_list`.
        """

        kwargs = {}
        init_form_list = SortedDict()
        kwargs['storage_name'] = storage_name

        assert len(form_list) > 0, 'at least one form is needed'

        for i in range(len(form_list)):
            form = form_list[i]
            if isinstance(form, tuple):
                init_form_list[unicode(form[0])] = form[1]
            else:
                init_form_list[unicode(i)] = form

        for form in init_form_list.values():
            if issubclass(form, formsets.BaseFormSet):
                form = form.form
            if [True for f in form.base_fields.values()
                if issubclass(f.__class__, forms.FileField)] and \
                not hasattr(cls, 'file_storage'):
                raise NoFileStorageException

        kwargs['form_list'] = init_form_list
        kwargs['initial_list'] = initial_list
        kwargs['instance_list'] = instance_list
        kwargs['condition_list'] = condition_list

        return kwargs

    def __repr__(self):
        return '%s: form_list: %s, initial_list: %s' % (
            self.get_wizard_name(), self.form_list, self.initial_list)

    def dispatch(self, request, *args, **kwargs):
        """
        This method gets called by the routing engine. The first argument is
        `request` which contains a `HttpRequest` instance.
        The request is stored in `self.request` for later use. The storage
        instance is stored in `self.storage`.

        After processing the request using the `dispatch` method, the
        response gets updated by the storage engine (for example add cookies).
        """

        self. storage = get_storage(
            self.storage_name,
            self.get_wizard_name(),
            request,
            getattr(self, 'file_storage', None)
        )
        response = super(FormWizard, self).dispatch(request, *args, **kwargs)
        self.storage.update_response(response)

        if kwargs.get('testmode', False):
            return response, self
        else:
            return response

    def get_form_list(self):
        form_list = SortedDict()
        for form_key, form_class in self.form_list.items():
            condition = self.condition_list.get(form_key, True)
            if callable(condition):
                condition = condition(self)
            if condition:
                form_list[form_key] = form_class
        return form_list

    def get(self, *args, **kwargs):
        """
        If the wizard gets a GET request, it assumes that the user just
        starts at the first step or wants to restart the process. The wizard
        will be resetted before rendering the first step.
        """
        self.reset_wizard()

        if 'extra_context' in kwargs:
            self.update_extra_context(kwargs['extra_context'])

        self.storage.set_current_step(self.get_first_step())
        return self.render(self.get_form())

    def post(self, *args, **kwargs):
        """
        Generates a HttpResponse which contains either the current step (if
        form validation wasn't successful), the next step (if the current step
        was stored successful) or the done view (if no more steps are
        available)
        """
        if 'extra_context' in kwargs:
            self.update_extra_context(kwargs['extra_context'])

        if self.request.POST.has_key('form_prev_step') and \
            self.get_form_list().has_key(self.request.POST['form_prev_step']):
            self.storage.set_current_step(self.request.POST['form_prev_step'])
            form = self.get_form(
                data=self.storage.get_step_data(self.determine_step()),
                files=self.storage.get_step_files(self.determine_step()),
            )
        else:
            # Check if form was refreshed
            current_step = self.determine_step()
            prev_step = self.get_prev_step(step=current_step)
            for value in self.request.POST:
                if prev_step and \
                    not value.startswith(current_step) and \
                    value.startswith(prev_step):
                    # form refreshed, change current step
                    self.storage.set_current_step(prev_step)
                    break

            form = self.get_form(data=self.request.POST,
                                 files=self.request.FILES)
            if form.is_valid():
                self.storage.set_step_data(self.determine_step(),
                                           self.process_step(form))
                self.storage.set_step_files(self.determine_step(),
                                            self.process_step_files(form))

                current_step = self.determine_step()
                last_step = self.get_last_step()

                if current_step == last_step:
                    return self.render_done(form, **kwargs)
                else:
                    return self.render_next_step(form)

        return self.render(form)

    def render_next_step(self, form, **kwargs):
        """
        Gets called when the next step/form should be rendered. `form`
        contains the last/current form.
        """
        next_step = self.get_next_step()
        new_form = self.get_form(next_step,
                                 data=self.storage.get_step_data(next_step),
                                 files=self.storage.get_step_files(next_step))
        self.storage.set_current_step(next_step)
        return self.render(new_form, **kwargs)

    def render_done(self, form, **kwargs):
        """
        Gets called when all forms passed. The method should also re-validate
        all steps to prevent manipulation. If any form don't validate,
        `render_revalidation_failure` should get called. If everything is fine
        call `done`.
        """
        final_form_list = []
        for form_key in self.get_form_list().keys():
            form_obj = self.get_form(
                step=form_key,
                data=self.storage.get_step_data(form_key),
                files=self.storage.get_step_files(form_key)
            )
            if not form_obj.is_valid():
                return self.render_revalidation_failure(form_key,
                                                        form_obj,
                                                        **kwargs)
            final_form_list.append(form_obj)
        done_response = self.done(final_form_list, **kwargs)
        self.reset_wizard()
        return done_response

    def get_form_prefix(self, step=None, form=None):
        """
        Returns the prefix which will be used when calling the actual form for
        the given step. `step` contains the step-name, `form` the form which
        will be called with the returned prefix.

        If no step is given, the form_prefix will determine the current step
        automatically.
        """
        if step is None:
            step = self.determine_step()
        return str(step)

    def get_form_initial(self, step):
        """
        Returns a dictionary which will be passed to the form for `step`
        as `initial`. If no initial data was provied while initializing the
        form wizard, a empty dictionary will be returned.
        """
        return self.initial_list.get(step, {})

    def get_form_instance(self, step):
        """
        Returns a object which will be passed to the form for `step`
        as `instance`. If no instance object was provied while initializing
        the form wizard, None be returned.
        """
        return self.instance_list.get(step, None)

    def get_form(self, step=None, data=None, files=None):
        """
        Constructs the form for a given `step`. If no `step` is defined, the
        current step will be determined automatically.

        The form will be initialized using the `data` argument to prefill the
        new form.
        """
        if step is None:
            step = self.determine_step()
        kwargs = {
            'data': data,
            'files': files,
            'prefix': self.get_form_prefix(step, self.form_list[step]),
            'initial': self.get_form_initial(step),
        }
        if issubclass(self.form_list[step], forms.ModelForm):
            kwargs.update({'instance': self.get_form_instance(step)})
        elif issubclass(self.form_list[step], forms.models.BaseModelFormSet):
            kwargs.update({'queryset': self.get_form_instance(step)})
        return self.form_list[step](**kwargs)

    def process_step(self, form):
        """
        This method is used to postprocess the form data. For example, this
        could be used to conditionally skip steps if a special field is
        checked. By default, it returns the raw `form.data` dictionary.
        """
        return self.get_form_step_data(form)

    def process_step_files(self, form):
        return self.get_form_step_files(form)

    def render_revalidation_failure(self, step, form, **kwargs):
        """
        Gets called when a form doesn't validate before rendering the done
        view. By default, it resets the current step to the first failing
        form and renders the form.
        """
        self.storage.set_current_step(step)
        return self.render(form, **kwargs)

    def get_form_step_data(self, form):
        """
        Is used to return the raw form data. You may use this method to
        manipulate the data.
        """
        return form.data

    def get_form_step_files(self, form):
        """
        Is used to return the raw form files. You may use this method to
        manipulate the data.
        """
        return form.files

    def get_all_cleaned_data(self):
        """
        Returns a merged dictionary of all step cleaned_data dictionaries.
        If a step contains a `FormSet`, the key will be prefixed with formset
        and contain a list of the formset' cleaned_data dictionaries.
        """
        cleaned_dict = {}
        for form_key in self.get_form_list().keys():
            form_obj = self.get_form(
                step=form_key,
                data=self.storage.get_step_data(form_key),
                files=self.storage.get_step_files(form_key)
            )
            if form_obj.is_valid():
                if isinstance(form_obj.cleaned_data, list):
                    cleaned_dict.update({
                        'formset-%s' % form_key: form_obj.cleaned_data
                    })
                else:
                    cleaned_dict.update(form_obj.cleaned_data)
        return cleaned_dict

    def get_cleaned_data_for_step(self, step):
        """
        Returns the cleaned data for a given `step`. Before returning the
        cleaned data, the stored values are being revalidated through the
        form. If the data doesn't validate, None will be returned.
        """
        if self.form_list.has_key(step):
            form_obj = self.get_form(step=step,
                                     data=self.storage.get_step_data(step),
                                     files=self.storage.get_step_files(step))
            if form_obj.is_valid():
                return form_obj.cleaned_data
        return None

    def determine_step(self):
        """
        Returns the current step. If no current step is stored in the storage
        backend, the first step will be returned.
        """
        return self.storage.get_current_step() or self.get_first_step()

    def get_first_step(self):
        """
        Returns the name of the first step.
        """
        return self.get_form_list().keys()[0]

    def get_last_step(self):
        """
        Returns the name of the last step.
        """
        return self.get_form_list().keys()[-1]

    def get_next_step(self, step=None):
        """
        Returns the next step after the given `step`. If no more steps are
        available, None will be returned. If the `step` argument is None, the
        current step will be determined automatically.
        """
        form_list = self.get_form_list()

        if step is None:
            step = self.determine_step()
        key = form_list.keyOrder.index(step) + 1
        if len(form_list.keyOrder) > key:
            return form_list.keyOrder[key]
        else:
            return None

    def get_prev_step(self, step=None):
        """
        Returns the previous step before the given `step`. If there are no
        steps available, None will be returned. If the `step` argument is
        None, the current step will be determined automatically.
        """
        form_list = self.get_form_list()

        if step is None:
            step = self.determine_step()
        key = form_list.keyOrder.index(step) - 1
        if key < 0:
            return None
        else:
            return form_list.keyOrder[key]

    def get_step_index(self, step=None):
        """
        Returns the index for the given `step` name. If no step is given,
        the current step will be used to get the index.
        """
        if step is None:
            step = self.determine_step()
        return self.get_form_list().keyOrder.index(step)

    def get_num_steps(self):
        """
        Returns the total number of steps/forms in this the wizard.
        """
        return len(self.get_form_list())

    def get_wizard_name(self):
        """
        Returns the name of the wizard. By default the class name is used.
        This name will be used in storage backends to prevent from colliding
        with other form wizards.
        """
        return self.__class__.__name__

    def reset_wizard(self):
        """
        Resets the user-state of the wizard.
        """
        self.storage.reset()

    def get_template(self):
        """
        Returns the templates to be used for rendering the wizard steps. This
        method can return a list of templates or a single string.
        """
        return 'formwizard/wizard.html'

    def get_template_context(self, form):
        """
        Returns the template context for a step. You can overwrite this method
        to add more data for all or some steps.
        Example:
        class MyWizard(FormWizard):
            def get_template_context(self, form):
                context = super(MyWizard, self).get_template_context(form)
                if self.storage.get_current_step() == 'my_step_name':
                    context.update({'another_var': True})
                return context
        """
        return {
            'extra_context': self.get_extra_context(),
            'form_step': self.determine_step(),
            'form_first_step': self.get_first_step(),
            'form_last_step': self.get_last_step(),
            'form_prev_step': self.get_prev_step(),
            'form_next_step': self.get_next_step(),
            'form_step0': int(self.get_step_index()),
            'form_step1': int(self.get_step_index()) + 1,
            'form_step_count': self.get_num_steps(),
            'form': form,
        }

    def get_extra_context(self):
        """
        Returns the extra data currently stored in the storage backend.
        """
        return self.storage.get_extra_context_data()

    def update_extra_context(self, new_context):
        """
        Updates the currently stored extra context data. Already stored extra
        context will be kept!
        """
        context = self.get_extra_context()
        context.update(new_context)
        return self.storage.set_extra_context_data(context)

    def render(self, form, **kwargs):
        """
        Renders the acutal `form`. This method can be used to pre-process data
        or conditionally skip steps.
        """
        return self.render_template(form)

    def render_template(self, form=None):
        """
        Returns a `HttpResponse` containing the rendered form step. Available
        template context variables are:

         * `extra_context` - current extra context data
         * `form_step` - name of the current step
         * `form_first_step` - name of the first step
         * `form_last_step` - name of the last step
         * `form_prev_step`- name of the previous step
         * `form_next_step` - name of the next step
         * `form_step0` - index of the current step
         * `form_step1` - index of the current step as a 1-index
         * `form_step_count` - total number of steps
         * `form` - form instance of the current step
        """

        form = form or self.get_form()
        return render_to_response(self.get_template(),
            self.get_template_context(form),
            context_instance=RequestContext(self.request))

    def done(self, form_list, **kwargs):
        """
        This method muss be overrided by a subclass to process to form data
        after processing all steps.
        """
        raise NotImplementedError("Your %s class has not defined a done() \
            method, which is required." % self.__class__.__name__)

class SessionFormWizard(FormWizard):
    """
    A FormWizard with pre-configured SessionStorageBackend.
    """
    @classonlymethod
    def as_view(cls, *args, **kwargs):
        return super(SessionFormWizard, cls).as_view(
            'formwizard.storage.session.SessionStorage', *args, **kwargs)

class CookieFormWizard(FormWizard):
    """
    A FormWizard with pre-configured CookieStorageBackend.
    """
    @classonlymethod
    def as_view(cls, *args, **kwargs):
        return super(CookieFormWizard, cls).as_view(
            'formwizard.storage.cookie.CookieStorage', *args, **kwargs)

class NamedUrlFormWizard(FormWizard):
    """
    A FormWizard with url-named steps support.
    """

    url_name = None
    done_step_name = None

    @classmethod
    def build_init_kwargs(cls, *args, **kwargs):
        """
        We require a url_name to reverse urls later. Additionally users can
        pass a done_step_name to change the url-name of the "done" view.
        """
        extra_kwargs = {
            'done_step_name': 'done'
        }

        assert kwargs.has_key('url_name'), \
            'url name is needed to resolve correct wizard urls'
        extra_kwargs['url_name'] = kwargs['url_name']
        del kwargs['url_name']

        if kwargs.has_key('done_step_name'):
            extra_kwargs['done_step_name'] = kwargs['done_step_name']
            del kwargs['done_step_name']

        initkwargs = super(NamedUrlFormWizard, cls).build_init_kwargs(
            *args, **kwargs)

        initkwargs.update(extra_kwargs)

        assert not initkwargs['form_list'].has_key(initkwargs['done_step_name']), \
            'step name "%s" is reserved for "done" view' % initkwargs['done_step_name']

        return initkwargs

    def get(self, *args, **kwargs):
        """
        This renders the form or, if needed, does the http redirects.
        """
        if not kwargs.has_key('step'):
            if self.request.GET.has_key('reset'):
                self.reset_wizard()
                self.storage.set_current_step(self.get_first_step())

            if 'extra_context' in kwargs:
                self.update_extra_context(kwargs['extra_context'])

            if self.request.GET:
                query_string = "?%s" % self.request.GET.urlencode()
            else:
                query_string = ""
            return HttpResponseRedirect(reverse(self.url_name,
                kwargs={'step': self.determine_step()}) + query_string)
        else:
            if 'extra_context' in kwargs:
                self.update_extra_context(kwargs['extra_context'])

            step_url = kwargs.get('step', None)

            # is the current step the "done" name/view?
            if step_url == self.done_step_name:
                return self.render_done(self.get_form(
                    step=self.get_last_step(),
                    data=self.storage.get_step_data(self.get_last_step()),
                    files=self.storage.get_step_files(self.get_last_step())
                ), **kwargs)

            # is the url step name not equal to the step in the storage?
            # if yes, change the step in the storage (if name exists)
            if step_url <> self.determine_step():
                if self.get_form_list().has_key(step_url):
                    self.storage.set_current_step(step_url)

                    return self.render(self.get_form(
                        data=self.storage.get_current_step_data(),
                        files=self.storage.get_current_step_files()
                    ), **kwargs)
                else:
                    # invalid step name, reset to first and redirect.
                    self.storage.set_current_step(self.get_first_step())

                    return HttpResponseRedirect(reverse(self.url_name,
                        kwargs={'step': self.storage.get_current_step()}))
            else:
                # url step name and storage step name are equal, render!
                return self.render(self.get_form(
                    data=self.storage.get_current_step_data(),
                    files=self.storage.get_current_step_files()
                ), **kwargs)

    def post(self, *args, **kwargs):
        """
        Do a redirect if user presses the prev. step button. The rest of this
        is super'd from FormWizard.
        """
        if self.request.POST.has_key('form_prev_step') and \
            self.get_form_list().has_key(self.request.POST['form_prev_step']):

            self.storage.set_current_step(self.request.POST['form_prev_step'])
            return HttpResponseRedirect(reverse(self.url_name, kwargs={
                'step': self.storage.get_current_step()
            }))
        else:
            return super(NamedUrlFormWizard, self).post(*args, **kwargs)

    def render_next_step(self, form, **kwargs):
        """
        When using the NamedUrlFormWizard, we have to redirect to update the
        browser's url to match the shown step.
        """
        next_step = self.get_next_step()
        self.storage.set_current_step(next_step)
        return HttpResponseRedirect(reverse(self.url_name,
                                            kwargs={'step': next_step}))

    def render_revalidation_failure(self, failed_step, form, **kwargs):
        """
        When a step fails, we have to redirect the user to the first failing
        step.
        """
        self.storage.set_current_step(failed_step)
        return HttpResponseRedirect(reverse(self.url_name, kwargs={
            'step': self.storage.get_current_step()
        }))

    def render_done(self, form, **kwargs):
        """
        When rendering the done view, we have to redirect first (if the url
        name doesn't fit).
        """
        step_url = kwargs.get('step', None)
        if step_url <> self.done_step_name:
            return HttpResponseRedirect(reverse(self.url_name, kwargs={
                'step': self.done_step_name
            }))

        return super(NamedUrlFormWizard, self).render_done(form, **kwargs)

class NamedUrlSessionFormWizard(NamedUrlFormWizard):
    """
    A NamedUrlFormWizard with pre-configured SessionStorageBackend.
    """
    @classonlymethod
    def as_view(cls, *args, **kwargs):
        return super(NamedUrlFormWizard, cls).as_view(
            'formwizard.storage.session.SessionStorage', *args, **kwargs)

class NamedUrlCookieFormWizard(NamedUrlFormWizard):
    """
    A NamedUrlFormWizard with pre-configured CookieStorageBackend.
    """
    @classonlymethod
    def as_view(cls, *args, **kwargs):
        return super(NamedUrlFormWizard, cls).as_view(
            'formwizard.storage.cookie.CookieStorage', *args, **kwargs)

