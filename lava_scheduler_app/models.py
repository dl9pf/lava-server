import logging
import os
import uuid
import simplejson
import urlparse
import datetime

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sites.models import Site
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.db import models
from django.template.loader import render_to_string
from django.utils.translation import ugettext as _
from django.shortcuts import get_object_or_404

from django_restricted_resource.models import RestrictedResource

from dashboard_app.models import Bundle, BundleStream

from lava_dispatcher.job import validate_job_data
from lava_scheduler_app import utils

from linaro_django_xmlrpc.models import AuthToken


class JSONDataError(ValueError):
    """Error raised when JSON is syntactically valid but ill-formed."""


class DevicesUnavailableException(UserWarning):
    """Error raised when required number of devices are unavailable."""


class Tag(models.Model):

    name = models.SlugField(unique=True)

    description = models.TextField(null=True, blank=True)

    def __unicode__(self):
        return self.name


def validate_job_json(data):
    try:
        ob = simplejson.loads(data)
        validate_job_data(ob)
    except ValueError, e:
        raise ValidationError(e)


class DeviceType(models.Model):
    """
    A class of device, for example a pandaboard or a snowball.
    """

    name = models.SlugField(primary_key=True)

    def __unicode__(self):
        return self.name

    health_check_job = models.TextField(
        null=True, blank=True, default=None, validators=[validate_job_json])

    display = models.BooleanField(default=True,
                                  help_text=("Should this be displayed in the GUI or not. This can be "
                                             "useful if you are removing all devices of this type but don't "
                                             "want to loose the test results generated by the devices."))

    @models.permalink
    def get_absolute_url(self):
        return ("lava.scheduler.device_type.detail", [self.pk])


class Device(models.Model):
    """
    A device that we can run tests on.
    """

    OFFLINE = 0
    IDLE = 1
    RUNNING = 2
    OFFLINING = 3
    RETIRED = 4
    RESERVED = 5

    STATUS_CHOICES = (
        (OFFLINE, 'Offline'),
        (IDLE, 'Idle'),
        (RUNNING, 'Running'),
        (OFFLINING, 'Going offline'),
        (RETIRED, 'Retired'),
        (RESERVED, 'Reserved')
    )

    # A device health shows a device is ready to test or not
    HEALTH_UNKNOWN, HEALTH_PASS, HEALTH_FAIL, HEALTH_LOOPING = range(4)
    HEALTH_CHOICES = (
        (HEALTH_UNKNOWN, 'Unknown'),
        (HEALTH_PASS, 'Pass'),
        (HEALTH_FAIL, 'Fail'),
        (HEALTH_LOOPING, 'Looping'),
    )

    hostname = models.CharField(
        verbose_name=_(u"Hostname"),
        max_length=200,
        primary_key=True,
    )

    device_type = models.ForeignKey(
        DeviceType, verbose_name=_(u"Device type"))

    device_version = models.CharField(
        verbose_name=_(u"Device Version"),
        max_length=200,
        null=True,
        default=None,
        blank=True,
    )

    current_job = models.ForeignKey(
        "TestJob", blank=True, unique=True, null=True, related_name='+',
        on_delete=models.SET_NULL)

    tags = models.ManyToManyField(Tag, blank=True)

    status = models.IntegerField(
        choices=STATUS_CHOICES,
        default=IDLE,
        verbose_name=_(u"Device status"),
    )

    health_status = models.IntegerField(
        choices=HEALTH_CHOICES,
        default=HEALTH_UNKNOWN,
        verbose_name=_(u"Device Health"),
    )

    last_health_report_job = models.ForeignKey(
        "TestJob", blank=True, unique=True, null=True, related_name='+',
        on_delete=models.SET_NULL)

    worker_hostname = models.CharField(
        verbose_name=_(u"Worker Hostname"),
        max_length=200,
        null=True,
        blank=True,
        default=None
    )

    last_heartbeat = models.DateTimeField(
        verbose_name=_(u"Last Heartbeat"),
        auto_now=False,
        auto_now_add=False,
        null=True,
        blank=True,
        editable=False
    )

    heartbeat = models.BooleanField(
        verbose_name=_(u"Heartbeat"),
        default=False)

    def __unicode__(self):
        return self.hostname

    @models.permalink
    def get_absolute_url(self):
        return ("lava.scheduler.device.detail", [self.pk])

    @models.permalink
    def get_device_health_url(self):
        return ("lava.scheduler.labhealth.detail", [self.pk])

    def recent_jobs(self):
        return TestJob.objects.select_related(
            "actual_device",
            "requested_device",
            "requested_device_type",
            "submitter",
            "user",
            "group",
        ).filter(
            actual_device=self
        ).order_by(
            '-start_time'
        )

    def can_admin(self, user):
        return user.has_perm('lava_scheduler_app.change_device')

    def put_into_maintenance_mode(self, user, reason, notify=None):
        if self.status in [self.RESERVED, self.OFFLINING]:
            new_status = self.OFFLINING
        elif self.status == self.RUNNING:
            if notify:
                # only one admin will be emailed when admin_notification is set.
                self.current_job.admin_notifications = notify
                self.current_job.save()
            new_status = self.OFFLINING
        else:
            new_status = self.OFFLINE
        DeviceStateTransition.objects.create(
            created_by=user, device=self, old_state=self.status,
            new_state=new_status, message=reason, job=None).save()
        self.status = new_status
        if self.health_status == Device.HEALTH_LOOPING:
            self.health_status = Device.HEALTH_UNKNOWN
        self.save()

    def put_into_online_mode(self, user, reason):
        if self.status == Device.OFFLINING:
            new_status = self.RUNNING
        else:
            new_status = self.IDLE
        DeviceStateTransition.objects.create(
            created_by=user, device=self, old_state=self.status,
            new_state=new_status, message=reason, job=None).save()
        self.status = new_status
        self.health_status = Device.HEALTH_UNKNOWN
        self.save()

    def put_into_looping_mode(self, user):
        if self.status not in [Device.OFFLINE, Device.OFFLINING]:
            return
        new_status = self.IDLE
        DeviceStateTransition.objects.create(
            created_by=user, device=self, old_state=self.status,
            new_state=new_status, message="Looping mode", job=None).save()
        self.status = new_status
        self.health_status = Device.HEALTH_LOOPING
        self.save()

    def cancel_reserved_status(self, user, reason):
        if self.status != Device.RESERVED:
            return
        new_status = self.IDLE
        DeviceStateTransition.objects.create(
            created_by=user, device=self, old_state=self.status,
            new_state=new_status, message=reason, job=None).save()
        self.status = new_status
        self.save()

    def too_long_since_last_heartbeat(self):
        """Calculates if the last_heartbeat is more than 180 seconds.

        If there is a delay update heartbeat value to False else True.
        """
        if self.last_heartbeat is None:
            self.last_heartbeat = datetime.datetime.utcnow()
        difference = datetime.datetime.utcnow() - self.last_heartbeat

        if difference.total_seconds() > 180:
            self.heartbeat = False
        else:
            self.heartbeat = True
        self.save()


class JobFailureTag(models.Model):
    """
    Allows us to maintain a set of common ways jobs fail. These can then be
    associated with a TestJob so we can do easy data mining
    """
    name = models.CharField(unique=True, max_length=256)

    description = models.TextField(null=True, blank=True)

    def __unicode__(self):
        return self.name


class TestJob(RestrictedResource):
    """
    A test job is a test process that will be run on a Device.
    """

    SUBMITTED = 0
    RUNNING = 1
    COMPLETE = 2
    INCOMPLETE = 3
    CANCELED = 4
    CANCELING = 5

    STATUS_CHOICES = (
        (SUBMITTED, 'Submitted'),
        (RUNNING, 'Running'),
        (COMPLETE, 'Complete'),
        (INCOMPLETE, 'Incomplete'),
        (CANCELED, 'Canceled'),
        (CANCELING, 'Canceling'),
    )

    LOW = 0
    MEDIUM = 50
    HIGH = 100

    PRIORITY_CHOICES = (
        (LOW, 'Low'),
        (MEDIUM, 'Medium'),
        (HIGH, 'High'),
    )

    id = models.AutoField(primary_key=True)

    sub_id = models.CharField(
        verbose_name=_(u"Sub ID"),
        blank=True,
        max_length=200
    )

    target_group = models.CharField(
        verbose_name=_(u"Target Group"),
        blank=True,
        max_length=64,
        null=True,
        default=None
    )

    submitter = models.ForeignKey(
        User,
        verbose_name=_(u"Submitter"),
        related_name='+',
    )

    submit_token = models.ForeignKey(
        AuthToken, null=True, blank=True, on_delete=models.SET_NULL)

    description = models.CharField(
        verbose_name=_(u"Description"),
        max_length=200,
        null=True,
        blank=True,
        default=None
    )

    health_check = models.BooleanField(default=False)

    # Only one of these two should be non-null.
    requested_device = models.ForeignKey(
        Device, null=True, default=None, related_name='+', blank=True)
    requested_device_type = models.ForeignKey(
        DeviceType, null=True, default=None, related_name='+', blank=True)

    tags = models.ManyToManyField(Tag, blank=True)

    # This is set once the job starts or is reserved.
    actual_device = models.ForeignKey(
        Device, null=True, default=None, related_name='+', blank=True)

    submit_time = models.DateTimeField(
        verbose_name=_(u"Submit time"),
        auto_now=False,
        auto_now_add=True
    )
    start_time = models.DateTimeField(
        verbose_name=_(u"Start time"),
        auto_now=False,
        auto_now_add=False,
        null=True,
        blank=True,
        editable=False
    )
    end_time = models.DateTimeField(
        verbose_name=_(u"End time"),
        auto_now=False,
        auto_now_add=False,
        null=True,
        blank=True,
        editable=False
    )

    @property
    def duration(self):
        if self.end_time is None:
            return None
        return self.end_time - self.start_time

    status = models.IntegerField(
        choices=STATUS_CHOICES,
        default=SUBMITTED,
        verbose_name=_(u"Status"),
    )

    priority = models.IntegerField(
        choices=PRIORITY_CHOICES,
        default=MEDIUM,
        verbose_name=_(u"Priority"),
    )

    definition = models.TextField(
        editable=False,
    )

    original_definition = models.TextField(
        editable=False,
        blank=True
    )

    multinode_definition = models.TextField(
        editable=False,
        blank=True
    )

    # only one value can be set as there is only one opportunity
    # to transition a device from Running to Offlining.
    admin_notifications = models.TextField(
        editable=False,
        blank=True
    )

    log_file = models.FileField(
        upload_to='lava-logs', default=None, null=True, blank=True)

    @property
    def output_dir(self):
        return os.path.join(settings.MEDIA_ROOT, 'job-output', 'job-%s' % self.id)

    def output_file(self):
        output_path = os.path.join(self.output_dir, 'output.txt')
        if os.path.exists(output_path):
            return open(output_path)
        elif self.log_file:
            log_file = self.log_file
            if log_file:
                try:
                    log_file.open()
                except IOError:
                    log_file = None
            return log_file
        else:
            return None

    failure_tags = models.ManyToManyField(
        JobFailureTag, blank=True, related_name='failure_tags')
    failure_comment = models.TextField(null=True, blank=True)

    _results_link = models.CharField(
        max_length=400, default=None, null=True, blank=True, db_column="results_link")

    _results_bundle = models.OneToOneField(
        Bundle, null=True, blank=True, db_column="results_bundle_id",
        on_delete=models.SET_NULL)

    @property
    def results_link(self):
        if self._results_bundle:
            return self._results_bundle.get_permalink()
        elif self._results_link:
            return self._results_link
        else:
            return None

    @property
    def multinode_role(self):
        if not self.is_multinode:
            return "Error"
        json_data = simplejson.loads(self.definition)
        if 'role' not in json_data:
            return "Error"
        return json_data['role']

    @property
    def results_bundle(self):
        if self._results_bundle:
            return self._results_bundle
        if not self.results_link:
            return None
        sha1 = self.results_link.strip('/').split('/')[-1]
        try:
            return Bundle.objects.get(content_sha1=sha1)
        except Bundle.DoesNotExist:
            return None

    def __unicode__(self):
        r = "%s test job" % self.get_status_display()
        if self.requested_device:
            r += " for %s" % (self.requested_device.hostname,)
        return r

    @models.permalink
    def get_absolute_url(self):
        return ("lava.scheduler.job.detail", [self.display_id])

    @classmethod
    def from_json_and_user(cls, json_data, user, health_check=False):
        job_data = simplejson.loads(json_data)
        validate_job_data(job_data)

        # Validate job, for parameters, specific to multinode that has been
        # input by the user. These parameters are reserved by LAVA and
        # generated during job submissions.
        reserved_job_params = ["group_size", "role", "sub_id", "target_group"]
        reserved_params_found = set(reserved_job_params).intersection(
            set(job_data.keys()))
        if reserved_params_found:
            raise JSONDataError("Reserved parameters found in job data %s" %
                                str([x for x in reserved_params_found]))

        # Get all device types that are available for scheduling.
        device_types = DeviceType.objects.values_list('name').filter(
            models.Q(device__status=Device.IDLE) |
            models.Q(device__status=Device.RUNNING) |
            models.Q(device__status=Device.RESERVED) |
            models.Q(device__status=Device.OFFLINE) |
            models.Q(device__status=Device.OFFLINING))\
            .annotate(num_count=models.Count('name')).order_by('name')

        # Count each of the device types available.
        all_devices = {}
        for dt in device_types:
            # dt[0] -> device type name
            # dt[1] -> device type count
            all_devices[dt[0]] = dt[1]

        if 'target' in job_data:
            try:
                target = Device.objects.filter(
                    ~models.Q(status=Device.RETIRED))\
                    .get(hostname=job_data['target'])
                device_type = None
            except Exception as e:
                raise DevicesUnavailableException(
                    "Requested device %s is unavailable." % job_data['target'])
        elif 'device_type' in job_data:
            target = None
            if all_devices.get(job_data['device_type'], 0) > 0:
                device_type = DeviceType.objects.get(
                    name=job_data['device_type'])
            else:
                raise DevicesUnavailableException(
                    "Device type '%s' is unavailable." %
                    (job_data['device_type']))
        elif 'device_group' in job_data:
            target = None
            device_type = None
            requested_devices = {}

            # Check if the requested devices are available for job run.
            for device_group in job_data['device_group']:
                device_type = device_group['device_type']
                count = device_group['count']
                if device_type in requested_devices:
                    requested_devices[device_type] += count
                else:
                    requested_devices[device_type] = count

            for board, count in requested_devices.iteritems():
                if all_devices.get(board, None) and \
                        count <= all_devices[board]:
                    continue
                else:
                    raise DevicesUnavailableException(
                        "Requested %d %s device(s) - only %d available." %
                        (count, board, all_devices.get(board, 0)))
        else:
            raise JSONDataError(
                "No 'target' or 'device_type' or 'device_group' are found "
                "in job data.")

        priorities = dict([(j.upper(), i) for i, j in cls.PRIORITY_CHOICES])
        priority = cls.MEDIUM
        if 'priority' in job_data:
            priority_key = job_data['priority'].upper()
            if priority_key not in priorities:
                raise JSONDataError("Invalid job priority: %r" % priority_key)
            priority = priorities[priority_key]

        for email_field in 'notify', 'notify_on_incomplete':
            if email_field in job_data:
                value = job_data[email_field]
                msg = ("%r must be a list of email addresses if present"
                       % email_field)
                if not isinstance(value, list):
                    raise ValueError(msg)
                for address in value:
                    if not isinstance(address, basestring):
                        raise ValueError(msg)
                    try:
                        validate_email(address)
                    except ValidationError:
                        raise ValueError(
                            "%r is not a valid email address." % address)

        if job_data.get('health_check', False) and not health_check:
            raise ValueError(
                "cannot submit a job with health_check: true via the api.")

        job_name = job_data.get('job_name', '')

        submitter = user
        group = None
        is_public = True

        for action in job_data['actions']:
            if not action['command'].startswith('submit_results'):
                continue
            stream = action['parameters']['stream']
            try:
                bundle_stream = BundleStream.objects.get(pathname=stream)
            except BundleStream.DoesNotExist:
                raise ValueError("stream %s not found" % stream)
            if not bundle_stream.can_upload(submitter):
                raise ValueError(
                    "you cannot submit to the stream %s" % stream)
            if not bundle_stream.is_anonymous:
                user, group, is_public = (bundle_stream.user,
                                          bundle_stream.group,
                                          bundle_stream.is_public)
            server = action['parameters']['server']
            parsed_server = urlparse.urlsplit(server)
            action["parameters"]["server"] = utils.rewrite_hostname(server)
            if parsed_server.hostname is None:
                raise ValueError("invalid server: %s" % server)

        tags = []
        for tag_name in job_data.get('device_tags', []):
            try:
                tags.append(Tag.objects.get(name=tag_name))
            except Tag.DoesNotExist:
                raise JSONDataError("tag %r does not exist" % tag_name)

        if 'device_group' in job_data:
            target_group = str(uuid.uuid4())
            node_json = utils.split_multi_job(job_data, target_group)
            job_list = []
            try:
                parent_id = (TestJob.objects.latest('id')).id + 1
            except:
                parent_id = 1
            child_id = 0

            for role in node_json:
                role_count = len(node_json[role])
                for c in range(0, role_count):
                    device_type = DeviceType.objects.get(
                        name=node_json[role][c]["device_type"])
                    sub_id = '.'.join([str(parent_id), str(child_id)])

                    # Add sub_id to the generated job dictionary.
                    node_json[role][c]["sub_id"] = sub_id

                    job = TestJob(
                        sub_id=sub_id, submitter=submitter,
                        requested_device=target, description=job_name,
                        requested_device_type=device_type,
                        definition=simplejson.dumps(node_json[role][c]),
                        original_definition=simplejson.dumps(json_data,
                                                             sort_keys=True,
                                                             indent=4 * ' '),
                        multinode_definition=json_data,
                        health_check=health_check, user=user, group=group,
                        is_public=is_public, priority=priority,
                        target_group=target_group)
                    job.save()
                    job_list.append(sub_id)
                    child_id += 1
            return job_list

        else:
            job_data = simplejson.dumps(job_data, sort_keys=True,
                                        indent=4 * ' ')
            job = TestJob(
                definition=job_data, original_definition=job_data,
                submitter=submitter, requested_device=target,
                requested_device_type=device_type, description=job_name,
                health_check=health_check, user=user, group=group,
                is_public=is_public, priority=priority)
            job.save()
            return job

    def _can_admin(self, user):
        """ used to check for things like if the user can cancel or annotate
        a job failure
        """
        return (user.is_superuser or user == self.submitter or
                user.has_perm('lava_scheduler_app.cancel_resubmit_testjob'))

    def can_annotate(self, user):
        """
        Permission required for user to add failure information to a job
        """
        states = [TestJob.COMPLETE, TestJob.INCOMPLETE, TestJob.CANCELED]
        return self._can_admin(user) and self.status in states

    def can_cancel(self, user):
        return self._can_admin(user) and self.status <= TestJob.RUNNING

    def can_resubmit(self, user):
        states = [TestJob.COMPLETE, TestJob.INCOMPLETE, TestJob.CANCELED]
        return self._can_admin(user) and self.status in states

    def cancel(self):
        # if SUBMITTED with actual_device - clear the actual_device back to idle.
        if self.status == TestJob.SUBMITTED and self.actual_device is not None:
            device = Device.objects.get(hostname=self.actual_device)
            device.cancel_reserved_status(self.submitter, "multinode-cancel")
        if self.status == TestJob.RUNNING:
            self.status = TestJob.CANCELING
        else:
            self.status = TestJob.CANCELED
        self.save()

    def _generate_summary_mail(self):
        domain = '???'
        try:
            site = Site.objects.get_current()
        except (Site.DoesNotExist, ImproperlyConfigured):
            pass
        else:
            domain = site.domain
        url_prefix = 'http://%s' % domain
        return render_to_string(
            'lava_scheduler_app/job_summary_mail.txt',
            {'job': self, 'url_prefix': url_prefix})

    def _get_notification_recipients(self):
        job_data = simplejson.loads(self.definition)
        recipients = job_data.get('notify', [])
        recipients.extend(self.admin_notifications)
        if self.status != self.COMPLETE:
            recipients.extend(job_data.get('notify_on_incomplete', []))
        return recipients

    def send_summary_mails(self):
        recipients = self._get_notification_recipients()
        if not recipients:
            return
        mail = self._generate_summary_mail()
        description = self.description.splitlines()[0]
        if len(description) > 200:
            description = description[197:] + '...'
        logger = logging.getLogger(self.__class__.__name__ + '.' + str(self.pk))
        logger.info("sending mail to %s", recipients)
        send_mail(
            "LAVA job notification: " + description, mail,
            settings.SERVER_EMAIL, recipients)

    @property
    def sub_jobs_list(self):
        if self.is_multinode:
            jobs = TestJob.objects.filter(
                target_group=self.target_group).order_by('id')
            return jobs
        else:
            return None

    @property
    def is_multinode(self):
        if self.target_group:
            return True
        else:
            return False

    @property
    def display_id(self):
        if self.sub_id:
            return self.sub_id
        else:
            return self.id

    @classmethod
    def get_by_job_number(cls, job_id):
        """If JOB_ID is of the form x.y ie., a multinode job notation, then
        query the database with sub_id and get the JOB object else use the
        given id as the primary key value.

        Returns JOB object.
        """
        if '.' in str(job_id):
            job = get_object_or_404(TestJob.objects, sub_id=job_id)
        else:
            job = get_object_or_404(TestJob.objects, pk=job_id)
        return job

    @property
    def display_definition(self):
        """If ORIGINAL_DEFINTION is stored in the database return it, for jobs
        which does not have ORIGINAL_DEFINTION ie., jobs that were submitted
        before this attribute was introduced, return the DEFINTION.
        """
        if self.original_definition and not self.is_multinode:
            return self.original_definition
        else:
            return self.definition


class DeviceStateTransition(models.Model):
    created_on = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    device = models.ForeignKey(Device, related_name='transitions')
    job = models.ForeignKey(TestJob, null=True, blank=True, on_delete=models.SET_NULL)
    old_state = models.IntegerField(choices=Device.STATUS_CHOICES)
    new_state = models.IntegerField(choices=Device.STATUS_CHOICES)
    message = models.TextField(null=True, blank=True)
