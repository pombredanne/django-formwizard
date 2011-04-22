from django import forms
from django.template import RequestContext
from django.shortcuts import render_to_response

from formwizard.forms import SessionFormWizard

class FeedbackStep1(forms.Form):
    name = forms.CharField()
    email = forms.EmailField()

class FeedbackStep2(forms.Form):
    support = forms.ChoiceField(choices=(
        ('like', 'like it'),
        ('dontlike', 'dont like it')
    ))
    performance = forms.ChoiceField(choices=(
        ('like', 'like it'),
        ('dontlike', 'dont like it')
    ))
    leave_message = forms.BooleanField(required=False)

class FeedbackStep3(forms.Form):
    message = forms.CharField(widget=forms.Textarea())

class FeedbackWizard(SessionFormWizard):
    def done(self, form_list):
        return render_to_response(
            'testapp/done.html',
            {'form_list': [form.cleaned_data for form in form_list]},
            context_instance=RequestContext(self.request)
        )

    def get_template(self):
        return ['testapp/form.html',]

    def show_message_form_condition(self):
         cleaned_data = self.get_cleaned_data_for_step('1') or {}
         return cleaned_data.get('leave_message', True)

feedback_form_instance = FeedbackWizard.as_view(
    [FeedbackStep1, FeedbackStep2, FeedbackStep3],
    condition_list={
        '2': FeedbackWizard.show_message_form_condition
    }
)

