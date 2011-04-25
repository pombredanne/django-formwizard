from django.conf.urls.defaults import *
from formwizard.tests.wizardtests.forms import (
    SessionContactWizard, CookieContactWizard, Page1, Page2, Page3, Page4)

urlpatterns = patterns('',
    url(r'^wiz_session/$', SessionContactWizard.as_view(
        [('form1', Page1),
         ('form2', Page2),
         ('form3', Page3),
         ('form4', Page4)])),
    url(r'^wiz_cookie/$', CookieContactWizard.as_view(
        [('form1', Page1),
         ('form2', Page2),
         ('form3', Page3),
         ('form4', Page4)])),
)
