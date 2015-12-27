import factory

from datetime import datetime

from django.utils import timezone

from ...models import TestModel


class TestModelFactory(factory.DjangoModelFactory):
    class Meta:
        model = TestModel

    character = 'Erik'
    integer = 1
    report_datetime = timezone.now()
    report_date = datetime.now()