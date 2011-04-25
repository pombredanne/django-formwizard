from django.test import TestCase

from formwizard.tests.storagetests import *
from formwizard.storage.session import SessionStorage

class TestSessionStorage(TestStorage, TestCase):
    def get_storage(self):
        return SessionStorage
