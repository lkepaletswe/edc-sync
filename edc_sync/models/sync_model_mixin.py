import socket

from django.conf import settings
from django.core import serializers
from django.core.exceptions import ImproperlyConfigured
from django.db import models, transaction
from django.db.models.loading import get_model
from django.utils import timezone

from edc_base.encrypted_fields import FieldCryptor

from ..exceptions import SyncError

from .outgoing_transaction import OutgoingTransaction
from django.db.utils import IntegrityError


class SyncModelMixin(models.Model):

    """Base model for all UUID models and adds synchronization
    methods and signals. """

    def __init__(self, *args, **kwargs):
        try:
            self.natural_key
        except AttributeError:
            raise SyncError('Model {}.{} is missing method natural_key '.format(
                self._meta.app_label, self._meta.model_name))
        try:
            self.__class__.objects.get_by_natural_key
        except AttributeError:
            raise SyncError('Model {}.{} is missing manager method get_by_natural_key '.format(
                self._meta.app_label, self._meta.model_name))
        super(SyncModelMixin, self).__init__(*args, **kwargs)

    def to_outgoing_transaction(self, using, created=None, deleted=None):
        """ Serializes the model instance to an encrypted json object
        and saves the json object to the OutgoingTransaction model."""
        created = True if created is None else created
        action = 'I' if created else 'U'
        if deleted:
            action = 'D'
        outgoing_transaction = None
        if self.is_serialized():
            assert using != 'default', self._meta.object_name
            outgoing_transaction = OutgoingTransaction.objects.using(using).create(
                tx_name=self._meta.object_name,
                tx_pk=self.id,
                tx=self.encrypted_json(),
                timestamp=timezone.now().strftime('%Y%m%d%H%M%S%f'),
                producer=self.sync_producer(using),
                action=action,
                using=using)
        return outgoing_transaction

    def sync_producer(self, using):
        Producer = get_model('edc_sync', 'producer')
        hostname = socket.gethostname()
        producer_name = '{}-{}'.format(hostname, using)
        try:
            Producer.objects.using(using).get(name=producer_name)
        except Producer.DoesNotExist:
            with transaction.atomic(using):
                try:
                    Producer.objects.using(using).create(
                        name=producer_name,
                        url='http://{}/'.format(hostname),
                        is_active=True,
                        settings_key=using)
                except IntegrityError:
                    pass
        return producer_name

    def is_serialized(self):
        """Returns the value of the settings.ALLOW_MODEL_SERIALIZATION or True.

        If True, this instance will serialized and saved to OutgoingTransaction.

        If this is an audit trail model instance, serialization be disabled if
        ALLOW_AUDIT_TRAIL_MODEL_SERIALIZATION=False. (default=True)"""
        try:
            is_serialized = settings.ALLOW_MODEL_SERIALIZATION
        except AttributeError:
            is_serialized = True
        if is_serialized:
            # TODO: does this work?? dont think so
            try:
                is_serialized = settings.ALLOW_AUDIT_TRAIL_MODEL_SERIALIZATION
            except AttributeError:
                is_serialized = True
        return is_serialized

    def encrypted_json(self):
        """Returns an encrypted json serialized from self."""
        json = serializers.serialize(
            "json", [self, ], ensure_ascii=False, use_natural_keys=True)
        return FieldCryptor('aes', 'local').encrypt(json)

    def skip_saving_criteria(self):
        """Returns True to skip saving, False to save (default).

        Users may override to avoid saving/persisting instances of a particular model that fit a certain
           criteria as defined in the subclass's overriding method.

        If there you want a certain model to not be persisted for what ever reason,
        (Usually to deal with temporary data cleaning issues) then define the method skip_saving_criteria()
        in your model which return True/False based on the criteria to be used for skipping.
        """
        False

#     def deserialize_prep(self, **kwargs):
#         """Users may override to manipulate the incoming object before calling save()"""
#         pass
#
#     def _deserialize_post(self, incoming_transaction):
#         """Default behavior for all subclasses of this class is to
#         serialize to outgoing transaction.
#
#         Note: this is necessary because a deserialized object will not create
#               an Outgoing Transaction by default since the "raw" parameter is True
#               on deserialization."""
#
#         if not settings.ALLOW_MODEL_SERIALIZATION:
#             raise SyncError(
#                 'Unexpectedly attempted to serialize even though settings.ALLOW_MODEL_SERIALIZATION=False')
#         try:
#             OutgoingTransaction.objects.get(pk=incoming_transaction.id)
#         except OutgoingTransaction.DoesNotExist:
#             OutgoingTransaction.objects.create(
#                 pk=incoming_transaction.id,
#                 tx_name=incoming_transaction.tx_name,
#                 tx_pk=incoming_transaction.tx_pk,
#                 tx=incoming_transaction.tx,
#                 timestamp=incoming_transaction.timestamp,
#                 producer=incoming_transaction.producer,
#                 action=incoming_transaction.action)
#         self.deserialize_post()
#
#     def deserialize_post(self):
#         """Users may override to do app specific tasks after deserialization."""
#         pass

    def deserialize_on_duplicate(self):
        """Users may override this to determine how to handle a duplicate
        error on deserialization.

        If you have a way to help decide if a duplicate should overwrite
        the existing record or not, evaluate your criteria here and return
        True or False. If False is returned to the deserializer, the
        object will not be saved and the transaction WILL be flagged
        as consumed WITHOUT error.
        """
        return True

    def deserialize_get_missing_fk(self, attrname):
        """Override to return a foreignkey object for 'attrname',
        if possible, using criteria in self, otherwise return None"""
        raise ImproperlyConfigured('Method deserialize_get_missing_fk() must '
                                   'be overridden on model class {0}'.format(self._meta.object_name))

    def save_to_inspector(self, fields, instance_pk, using):
        """Override in concrete class."""
        return False

    class Meta:
        abstract = True
