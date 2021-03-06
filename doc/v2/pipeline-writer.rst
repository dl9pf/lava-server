Test Writer use cases
#####################

.. index:: writing pipeline submission

.. _writing_pipeline_submission:

Writing a pipeline job submission
*********************************

Numerous changes have been made compared with the previous JSON
submission format. There is no compatibility between the old JSON files
and the new pipeline job submissions. There is no conversion tool and
none will be supported. Each test job needs to be understood and
redesigned. Compatibility has only been preserved inside the Lava Test
Shell Definitions.

.. _general_pipeline_principles:

General principles
==================

API
---

#. The API here is still in development and changes may still be
   required. `Sample jobs are available <https://git.linaro.org/lava-team/refactoring.git>`_
   from the LAVA team and are updated regularly.
#. Only certain deployment types, boot types and device types are currently
   supportable. These guidelines will be enlarged as support grows.
#. The pipeline does not make assumptions and the only defaults are
   constrictive and only provided for admin reasons.

Validity checks
---------------

#. Decisions about the validity of a test job submission are made as early
   as possible.
#. Some validity checks will be done before a job submission is accepted
#. Most will be completed before the job submission is scheduled.
#. An invalid job submission will result in an Incomplete test job.
#. Some validity checks can only be done after the test job has started,
   e.g. checks relating to downloaded files. These checks will result in
   a JobError.

Results
-------

#. All pipeline test jobs report results, whether a test shell is used
   or not.
#. Visibility of test job results is determined solely by the job submission.
#. Results are part of the test job and cannot be manully created.
#. Result analysis is primarily a task for other engines, results can
   be exported in full but the principle emphasis is on data generation.
#. Results are posted in real time, whilst the job is still running. This means
   that a later failure in the test job cannot cause a loss of results.

Job submission data
-------------------

#. There are three actions for all test jobs: **deploy**, **boot** and **test**.
#. All scheduled submissions may only specify a :term:`device type`,
   **not** a target. The device type is only for use by the scheduler and
   is ignored by the dispatcher. Locally, dispatchers only have configuration
   for the devices currently running test jobs.
#. Default timeouts can be specified at the top of the file.
#. priority can be specified, the default is medium.
#. **Always** `check your YAML syntax <http://yaml-online-parser.appspot.com/?yaml=>`_
#. The **actions** element in a pipeline job submission is a list of
   dictionaries - ensure that the line ends with a colon ``:``, the next
   line needs to be indented and needs to start with a hyphen ``-``.
#. YAML supports comments using ``#``, please use them liberally. Comments
   are not preserved in the database after submission.
#. The new result views know about the deployment type and boot type,
   so the ``job_name`` can concentrate on the objective of the test, not the
   methods used in it. The job name will still need to exist as a file
   in the test shell and as a URL in the results, so underscores and
   hyphens need to be used in place of spaces.

.. note:: :ref:`dispatcher_timeouts` are specified in human readable
    units, days, hours, minutes or seconds. Avoid specifying timeouts
    in smaller units when a larger unit is available: i.e. you should
    **never** use 120 seconds, always 2 minutes. Schema rules may be
    introduced to enforce this and your jobs could be rejected. The
    requested timeout and the actual duration of each action class within
    a test job is logged and excessive timeouts can be identified.

Writing a new TestJob
=====================

See :ref:`new_dispatcher_actions` for details of the available actions
and use the `sample jobs <https://git.linaro.org/lava-team/refactoring.git>`_
as examples.

Understanding available support
===============================

Devices to run pipeline jobs must be set as a pipeline device by the
admin of the LAVA instance. Check for a tick mark in the Pipeline Device
column of the device type overview. The instance itself must be enabled
for pipeline usage - one indicator is that an updated instance has a
**Results** section in the top level menu.

Understanding a TestJob
=======================

To convert an existing job to the pipeline, there are steps to be
covered:

#. Be explicit about the type of deployment and the type of boot
#. Be explicit about the operating system inside any rootfs
#. Start with an already working device type or job submission.
#. Start with singlenode jobs, use of the multinode protocol can follow later.
#. Drop details of submitting results

Instead of calling a "deploy_kernel" or "deploy_image" command and passing
parameters, a pipeline job submission requires that the type of
deployment and the type of boot is specified as part of a single deploy
action which covers all devices and all jobs.

Equally, a pipeline job submission requires that assumptions are removed
in favour of explicit settings. Just because a URL ends in ``.gz`` does
not mean that the dispatcher will assume that ``gz`` decompression can
be used - this must be specified or no decompression is done at all.

The pipeline will not assume anything about the operating system of
a rootfs specified in a URL - the job submission will simply fail to
validate and will be rejected.

Booting beaglebone-black with an nfsrootfs requires knowledge of how
that device can use NFS - in this case, using tftp.

.. code-block:: yaml

 actions:
  - deploy:
      to: tftp
      kernel: http://images.validation.linaro.org/functional-test-images/bbb/zImage
      # nfsrootfs: file:///home/linaro/lava/nfsrootfs/jessie-rootfs.tar.gz
      nfsrootfs: http://images.validation.linaro.org/pipeline/debian-jessie-rootfs.tar.gz
      os: debian
      dtb: http://images.validation.linaro.org/functional-test-images/bbb/am335x-bone.dtb

.. note:: the use of comments here allows the writer to flip between a remote image
   and a local test version of that image - this would be suitable for running
   directly on a local dispatcher.

The same deployment stanza can be used for any device which supports
NFS using tftp, just changing the URLs.

To change this deployment to a ramdisk without NFS, still using TFTP,
simply provide a ramdisk instead of an nfsrootfs:

.. code-block:: yaml

 actions:

  - deploy:
     to: tftp
     kernel: http://images.validation.linaro.org/functional-test-images/bbb/zImage
     ramdisk: http://images.validation.linaro.org/functional-test-images/common/linaro-image-minimal-initramfs-genericarmv7a.cpio.gz.u-boot
     ramdisk-type: u-boot
     os: oe
     dtb: http://images.validation.linaro.org/functional-test-images/bbb/am335x-bone.dtb

.. note:: **ramdisk-type** must be explicitly set, despite the URL in this
   case happening to have a ``u-boot`` extension. This is not assumed.
   Without the ``ramdisk-type`` being set to ``u-boot`` in the job submission,
   the UBoot header on the ramdisk would be mangled when the test definitions
   are applied, resulting in an invalid ramdisk.
