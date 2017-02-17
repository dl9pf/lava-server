import re
from voluptuous import (
    Schema, Required, All, Length,
    Any, Invalid, Optional, MultipleInvalid
)


class SubmissionException(UserWarning):
    """ Error raised if the submission is itself invalid. """


def _timeout_schema():
    return Schema({
        'days': int, 'hours': int, 'minutes': int, 'seconds': int
    })


def _deploy_tftp_schema():
    return Schema({
        Required('to'): 'tftp',
        Optional('kernel'): {Required('url'): str},
        Optional('ramdisk'): {Required('url'): str},
        Optional('initrd'): {Required('url'): str},
        Optional('nbdroot'): {Required('url'): str},
        Optional('nfsrootfs'): {Required('url'): str},
        Optional('dtb'): {Required('url'): str},
    }, extra=True)


def _job_deploy_schema():
    return Schema({
        Required('to'): str,
        Optional('timeouts'): _timeout_schema(),
    }, extra=True)


def _auto_login_schema():
    return Schema({
        Optional('login_prompt'): str,
        Optional('username'): str,
        Optional('password_prompt'): str,
        Optional('password'): str
    })


def _simple_params():
    return Schema({
        Any(str): str
    })


def _context_schema():
    return Schema({
        Optional('arch'): str,
        Optional('memory'): int,
        Optional('netdevice'): str,
        Optional('extra_options'): list
    }, extra=True)


def _job_boot_schema():
    return Schema({
        Required('method'): str,
        Optional('timeouts'): _timeout_schema(),
        Optional('auto_login'): _auto_login_schema(),
        Optional('parameters'): _simple_params(),
    }, extra=True)


def _inline_schema():
    return Schema({
        'metadata': dict,
        'install': dict,
        'run': dict,
        'parse': dict
    })


def _job_definition_schema():
    return Schema([
        {
            Required('repository'): Any(_inline_schema(), str),
            Required('from'): str,
            Required('name'): str,
            Required('path'): str,
            Optional('parameters'): dict,
        }
    ], extra=True)


def _job_test_schema():
    return Schema({
        Required('definitions'): _job_definition_schema(),
        Optional('timeouts'): _timeout_schema(),
    }, extra=True)


def _job_monitor_schema():
    return Schema({
        Required('monitors'): _monitor_def_schema(),
        Optional('timeouts'): _timeout_schema()
    }, extra=True)


def _monitor_def_schema():
    return Schema([
        {
            Required('name'): str,
            Required('start'): str,
            Required('end'): str,
            Required('pattern'): str,
            Optional('fixupdict'): dict
        }
    ])


def _job_actions_schema():
    return Schema([
        {
            'deploy': Any(
                _deploy_tftp_schema(),
                _job_deploy_schema()),
            'boot': _job_boot_schema(),
            'test': Any(_job_monitor_schema(),
                        _job_test_schema())
        }
    ])


def _job_notify_schema():
    return Schema({
        Required('criteria'): _notify_criteria_schema(),
        'recipients': _recipient_schema(),
        'verbosity': Any('verbose', 'quiet', 'status-only'),
        'compare': _notify_compare_schema()
    }, extra=True)


def _recipient_schema():
    from lava_scheduler_app.models import NotificationRecipient
    return Schema([
        {
            Required('to'): {
                Required('method'): Any(NotificationRecipient.EMAIL_STR,
                                        NotificationRecipient.IRC_STR),
                'user': str,
                'email': str,
                'server': str,
                'handle': str
            }
        }
    ])


def _notify_criteria_schema():
    return Schema({
        Required('status'): Any('running', 'complete', 'incomplete',
                                'canceled'),
        'type': Any('progression', 'regression')
    }, extra=True)


def _notify_compare_schema():
    return Schema({
        'query': Any(_query_name_schema(), _query_conditions_schema()),
        'blacklist': [str]
    }, extra=True)


def _query_name_schema():
    return Schema({
        Required('username'): str,
        Required('name'): str
    })


def _query_conditions_schema():
    return Schema({
        Required('entity'): str,
        'conditions': dict
    })


def vlan_name(value):
    if re.match("^[_a-zA-Z0-9]+$", str(value)):
        return str(value)
    else:
        raise Invalid(value)


def _job_protocols_schema():
    return Schema({
        'lava-multinode': {
            'timeout': _timeout_schema(),
            'roles': dict
        },
        'lava-vland': {
            str: {
                vlan_name: {
                    'tags': [
                        str
                    ],
                }
            }
        },
        'lava-lxc': dict
    })


def _job_timeout_schema():
    return Schema({
        Required('job'): _timeout_schema(),
        Required('action'): _timeout_schema(),
        'connection': _timeout_schema(),
    }, extra=True)


def visibility_schema():
    # possible values - 1 of 2 strings or a specified dict
    return Schema(Any('public', 'personal', {'group': [str]}))


def _job_schema():
    return Schema(
        {
            'device_type': All(str, Length(min=1)),  # not Required as some protocols encode it elsewhere
            Required('job_name'): All(str, Length(min=1, max=200)),
            'priority': Any('high', 'medium', 'low'),
            'protocols': _job_protocols_schema(),
            'context': _context_schema(),
            'metadata': dict,
            'secrets': dict,
            'tags': [str],
            Required('visibility'): visibility_schema(),
            Required('timeouts'): _job_timeout_schema(),
            Required('actions'): _job_actions_schema(),
            'notify': _job_notify_schema()
        }
    )


def _device_deploy_schema():
    return Schema({
        'connections': dict,
        Required('methods'): dict,
        Optional('parameters'): _simple_params(),
    })


def _device_boot_schema():
    return Schema({
        Required('connections'): dict,
        Required('methods'): dict,
    })


def _device_actions_schema():
    return Schema({
        'deploy': _device_deploy_schema(),
        'boot': _device_boot_schema(),
    })


def _device_timeouts_schema():
    return Schema({
        'actions': {
            All(str): _timeout_schema()
        },
        'connections': {
            All(str): _timeout_schema()
        },
    }, extra=True)


def _device_schema():
    """
    Less strict than the job_schema as this is primarily admin / template controlled.
    """
    return Schema({
        'character_delays': dict,
        'commands': dict,
        'adb_serial_number': str,
        'fastboot_serial_number': str,
        'fastboot_via_uboot': bool,
        'device_info': [dict],
        'flash_cmds_order': list,
        'device_type': All(str, Length(min=1)),
        'parameters': dict,
        'board_id': str,
        'usb_vendor_id': All(str, Length(min=4, max=4)),  # monitor type like arduino
        'usb_product_id': All(str, Length(min=4, max=4)),  # monitor type like arduino
        'usb_serial_driver': str,
        'actions': _device_actions_schema(),
        'timeouts': _device_timeouts_schema()
    })


def _validate_secrets(data_object):
    if 'secrets' in data_object:
        if data_object['visibility'] == 'public':
            raise SubmissionException("When 'secrets' is used, 'visibility' shouldn't be 'public'")


def validate_submission(data_object):
    """
    Validates a python object as a TestJob submission
    :param data: Python object, e.g. from yaml.load()
    :return: True if valid, else raises SubmissionException
    """
    try:
        schema = _job_schema()
        schema(data_object)
    except MultipleInvalid as exc:
        raise SubmissionException(exc)

    _validate_secrets(data_object)
    return True


def validate_device(data_object):
    """
    Validates a python object as a pipeline device configuration
    e.g. yaml.load(`lava-server manage device-dictionary --hostname host1 --export`)
    To validate a device_type template, a device dictionary needs to be created.
    :param data: Python object representing a pipeline Device.
    :return: True if valid, else raises SubmissionException
    """
    try:
        schema = _device_schema()
        schema(data_object)
    except MultipleInvalid as exc:
        raise SubmissionException(exc)

    return True
