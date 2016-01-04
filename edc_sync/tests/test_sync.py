import socket

from django.core.exceptions import MultipleObjectsReturned
from django.db import models
from django.test.testcases import TestCase
from django.test.utils import override_settings

from edc_base.model.models import BaseUuidModel
from edc_device import Device
from edc_sync.exceptions import SyncError
from edc_sync.models import SyncModelMixin, OutgoingTransaction
from edc_sync.models.incoming_transaction import IncomingTransaction

from .test_models import TestModel, ComplexTestModel, Fk, M2m
from edc_sync.models.producer import Producer


class BadTestModel(SyncModelMixin, BaseUuidModel):
    """A test model that is missing natural_key and get_by_natural_key."""

    f1 = models.CharField(max_length=10, default='f1')

    objects = models.Manager()

    class Meta:
        app_label = 'edc_sync'


class AnotherBadTestModel(SyncModelMixin, BaseUuidModel):
    """A test model that is missing get_by_natural_key."""

    f1 = models.CharField(max_length=10, default='f1')

    objects = models.Manager()

    def natural_key(self):
        return (self.f1, )

    class Meta:
        app_label = 'edc_sync'


class TestSync(TestCase):

    multi_db = True

    def get_credentials(self):
        return self.create_apikey(username=self.username, api_key=self.api_client_key)

    def test_raises_on_missing_natural_key(self):
        with self.assertRaises(SyncError) as cm:
            BadTestModel.objects.using('client').create()
        self.assertIn('natural_key', str(cm.exception))

    def test_raises_on_missing_get_by_natural_key(self):
        with self.assertRaises(SyncError) as cm:
            AnotherBadTestModel.objects.using('client').create()
        self.assertIn('get_by_natural_key', str(cm.exception))

    def test_creates_outgoing_on_add(self):
        test_model = TestModel.objects.using('client').create(f1='erik')
        with self.assertRaises(OutgoingTransaction.DoesNotExist):
            try:
                OutgoingTransaction.objects.using('client').get(
                    tx_pk=test_model.pk, tx_name='TestModel', action='I')
            except OutgoingTransaction.DoesNotExist:
                pass
            else:
                raise OutgoingTransaction.DoesNotExist()
        with self.assertRaises(OutgoingTransaction.DoesNotExist):
            try:
                OutgoingTransaction.objects.using('client').get(
                    tx_pk=test_model.pk, tx_name='TestModelAudit', action='I')
            except OutgoingTransaction.DoesNotExist:
                pass
            else:
                raise OutgoingTransaction.DoesNotExist()

    @override_settings(ALLOW_MODEL_SERIALIZATION=False)
    def test_does_not_create_outgoing(self):
        test_model = TestModel.objects.using('client').create(f1='erik')
        with self.assertRaises(OutgoingTransaction.DoesNotExist):
            OutgoingTransaction.objects.using('client').get(tx_pk=test_model.pk)

    def test_creates_outgoing_on_change(self):
        test_model = TestModel.objects.using('client').create(f1='erik')
        test_model.save(using='client')
        with self.assertRaises(OutgoingTransaction.DoesNotExist):
            try:
                OutgoingTransaction.objects.using('client').get(tx_pk=test_model.pk, tx_name='TestModel', action='I')
                OutgoingTransaction.objects.using('client').get(tx_pk=test_model.pk, tx_name='TestModel', action='U')
            except OutgoingTransaction.DoesNotExist:
                pass
            else:
                raise OutgoingTransaction.DoesNotExist()
        self.assertEqual(
            2, OutgoingTransaction.objects.using('client').filter(
                tx_pk=test_model.pk, tx_name='TestModelAudit', action='I').count())

    def test_timestamp_is_default_order(self):
        test_model = TestModel.objects.using('client').create(f1='erik')
        test_model.save(using='client')
        last = 0
        for obj in OutgoingTransaction.objects.using('client').all():
            self.assertGreater(int(obj.timestamp), last)
            last = int(obj.timestamp)

    def test_deserialize_fails_not_server(self):
        device = Device(device_id='10')
        self.assertFalse(device.is_server)
        TestModel.objects.using('client').create(f1='erik')
        self.assertRaises(
            SyncError,
            IncomingTransaction.objects.using('server').filter(
                is_consumed=False).deserialize, custom_device=device)

    def test_deserialize_succeeds_as_server(self):
        device = Device(device_id='99')
        self.assertTrue(device.is_server)
        TestModel.objects.using('client').create(f1='erik')
        with self.assertRaises(SyncError):
            try:
                IncomingTransaction.objects.using('server').filter(
                    is_consumed=False).deserialize(custom_device=device)
            except:
                pass
            else:
                raise SyncError()

    def test_copy_db_to_db(self):
        TestModel.objects.using('client').create(f1='erik')
        self.assertEqual(
            IncomingTransaction.objects.using('server').all().count(), 0)
        OutgoingTransaction.objects.using('client').all().copy_to_incoming_transaction('server')
        self.assertEquals(
            OutgoingTransaction.objects.using('client').all().count(),
            IncomingTransaction.objects.using('server').all().count())

    def test_deserialize_insert(self):
        device = Device(device_id='99')
        TestModel.objects.using('client').create(f1='erik')
        OutgoingTransaction.objects.using('client').all().copy_to_incoming_transaction('server')
        messages = IncomingTransaction.objects.using('server').filter(
            is_consumed=False).deserialize(custom_device=device, check_hostname=False)
        self.assertEqual(3, len(messages))
        for message in messages:
            self.assertEqual((1, 0, 0), (message.inserted, message.updated, message.deleted))
        with self.assertRaises(TestModel.DoesNotExist):
            try:
                TestModel.objects.using('server').get(f1='erik')
            except:
                pass
            else:
                raise TestModel.DoesNotExist

    def test_deserialize_update(self):
        device = Device(device_id='99')
        test_model = TestModel.objects.using('client').create(f1='erik')
        OutgoingTransaction.objects.using('client').all().copy_to_incoming_transaction('server')
        IncomingTransaction.objects.using('server').filter(
            is_consumed=False).deserialize(custom_device=device, check_hostname=False)
        self.assertEqual(0, IncomingTransaction.objects.using('server').filter(is_consumed=False).count())
        test_model.save(using='client')
        OutgoingTransaction.objects.using('client').filter(
            is_consumed_server=False).copy_to_incoming_transaction('server')
        messages = IncomingTransaction.objects.using('server').filter(
            is_consumed=False).deserialize(custom_device=device, check_hostname=False)
        self.assertEqual(2, len(messages))
        for message in messages:
            if message.tx_name == 'TestModel':
                self.assertEqual((0, 1, 0), (message.inserted, message.updated, message.deleted))
            if message.tx_name == 'TestModelAudit':
                self.assertEqual((1, 0, 0), (message.inserted, message.updated, message.deleted))

        with self.assertRaises(TestModel.DoesNotExist):
            try:
                TestModel.objects.using('server').get(f1='erik')
            except:
                pass
            else:
                raise TestModel.DoesNotExist

    def test_created_obj_serializes_to_correct_db(self):
        """Asserts that the obj and the audit obj serialize to the correct DB in a multi-database environment."""
        TestModel.objects.using('client').create(f1='erik')
        self.assertListEqual(
            [obj.tx_name for obj in OutgoingTransaction.objects.using('client').all()],
            [u'TestModel', u'Producer', u'TestModelAudit'])
        self.assertListEqual([obj.tx_name for obj in OutgoingTransaction.objects.using('server').all()], [])
        self.assertRaises(OutgoingTransaction.DoesNotExist,
                          OutgoingTransaction.objects.using('server').get, tx_name='TestModel')
        self.assertRaises(
            MultipleObjectsReturned,
            OutgoingTransaction.objects.using('client').get, tx_name__contains='TestModel')

    def test_updated_obj_serializes_to_correct_db(self):
        """Asserts that the obj and the audit obj serialize to the correct DB in a multi-database environment."""
        test_model = TestModel.objects.using('client').create(f1='erik')
        self.assertListEqual(
            [obj.tx_name for obj in OutgoingTransaction.objects.using('client').filter(action='I')],
            [u'TestModel', u'Producer', u'TestModelAudit'])
        self.assertListEqual(
            [obj.tx_name for obj in OutgoingTransaction.objects.using('client').filter(action='U')],
            [])
        test_model.save(using='client')
        self.assertListEqual(
            [obj.tx_name for obj in OutgoingTransaction.objects.using('client').filter(action='U')],
            [u'TestModel'])
        self.assertListEqual(
            [obj.tx_name for obj in OutgoingTransaction.objects.using('client').filter(action='I')],
            [u'TestModel', u'Producer', u'TestModelAudit', u'TestModelAudit'])

    def test_complex_model_works_for_fk(self):
        with override_settings(DEVICE_ID='99'):
            device = Device(device_id='99')
            for name in 'abcdefg':
                fk = Fk.objects.using('client').create(name=name)
            ComplexTestModel.objects.using('client').create(f1='1', fk=fk)
            OutgoingTransaction.objects.using('client').filter(
                is_consumed_server=False).copy_to_incoming_transaction('server')
            IncomingTransaction.objects.using('server').filter(
                is_consumed=False).deserialize(custom_device=device, check_hostname=False)
            self.assertEqual(IncomingTransaction.objects.using('server').filter(
                is_consumed=False).count(), 0)
            ComplexTestModel.objects.using('server').get(f1='1', fk__name=fk.name)

    def test_deserialization_messages_inserted(self):
        device = Device(device_id='99')
        for name in 'abcdefg':
            fk = Fk.objects.using('client').create(name=name)
        ComplexTestModel.objects.using('client').create(f1='1', fk=fk)
        OutgoingTransaction.objects.using('client').all().copy_to_incoming_transaction('server')
        messages = IncomingTransaction.objects.using('server').filter(
            is_consumed=False).deserialize(custom_device=device, check_hostname=False)
        self.assertEqual(sum([msg.inserted for msg in messages]), 10)

    def test_deserialization_messages_updated(self):
        device = Device(device_id='99')
        for name in 'abcdefg':
            fk = Fk.objects.using('client').create(name=name)
        complex_test_model = ComplexTestModel.objects.using('client').create(f1='1', fk=fk)
        OutgoingTransaction.objects.using('client').all().copy_to_incoming_transaction('server')
        IncomingTransaction.objects.using('server').filter(
            is_consumed=False).deserialize(custom_device=device, check_hostname=False)
        complex_test_model.save(using='client')
        OutgoingTransaction.objects.using('client').filter(
            is_consumed_server=False).copy_to_incoming_transaction('server')
        messages = IncomingTransaction.objects.using('server').filter(
            is_consumed=False).deserialize(custom_device=device, check_hostname=False)
        self.assertEqual(sum([msg.updated for msg in messages]), 1)

    def test_deserialization_updates_incoming_is_consumed(self):
        device = Device(device_id='99')
        for name in 'abcdefg':
            fk = Fk.objects.using('client').create(name=name)
        ComplexTestModel.objects.using('client').create(f1='1', fk=fk)
        OutgoingTransaction.objects.using('client').all().copy_to_incoming_transaction('server')
        IncomingTransaction.objects.using('server').filter(
            is_consumed=False).deserialize(custom_device=device, check_hostname=False)
        self.assertEqual(IncomingTransaction.objects.using('server').filter(
            is_consumed=False).count(), 0)

    def test_deserialize_with_m2m(self):
        device = Device(device_id='99')
        for name in 'abcdefg':
            fk = Fk.objects.using('client').create(name=name)
        for name in 'hijklmnop':
            M2m.objects.using('client').create(name=name)
        complex_model = ComplexTestModel.objects.using('client').create(f1='1', fk=fk)
        complex_model.m2m.add(M2m.objects.using('client').first())
        complex_model.m2m.add(M2m.objects.using('client').last())
        complex_model = ComplexTestModel.objects.using('client').get(f1='1')
        self.assertEqual(complex_model.m2m.using('client').all().count(), 2)
        OutgoingTransaction.objects.using('client').all().copy_to_incoming_transaction('server')
        IncomingTransaction.objects.using('server').filter(
            is_consumed=False).deserialize(custom_device=device, check_hostname=False)
        complex_model = ComplexTestModel.objects.using('server').get(f1='1', fk__name=fk.name)
        self.assertEqual(complex_model.m2m.using('client').all().count(), 2)

    def test_deserialize_with_missing_m2m(self):
        device = Device(device_id='99')
        for name in 'abcdefg':
            fk = Fk.objects.using('client').create(name=name)
        for name in 'hijklmnop':
            M2m.objects.using('client').create(name=name)
        complex_model = ComplexTestModel.objects.using('client').create(f1='1', fk=fk)
        complex_model.m2m.add(M2m.objects.using('client').first())
        complex_model.m2m.add(M2m.objects.using('client').last())
        complex_model = ComplexTestModel.objects.using('client').get(f1='1')
        OutgoingTransaction.objects.using('client').all().copy_to_incoming_transaction('server')
        IncomingTransaction.objects.using('server').filter(
            is_consumed=False).deserialize(custom_device=device, check_hostname=False)
        complex_model = ComplexTestModel.objects.using('server').get(f1='1', fk__name=fk.name)
        self.assertEqual(complex_model.m2m.all().count(), 2)

    def test_creates_producer(self):
        device = Device(device_id='99')
        TestModel.objects.using('client').create(f1='erik')
        self.assertEqual(Producer.objects.using('client').all().count(), 1)
        print(Producer.objects.using('client').first().__dict__)
        Producer.objects.using('client').get(name='{}-{}'.format(socket.gethostname(), 'client'))
        # OutgoingTransaction.objects.using('client').all().copy_to_incoming_transaction('server')
