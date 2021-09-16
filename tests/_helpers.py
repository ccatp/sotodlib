# Copyright (c) 2018-2019 Simons Observatory.
# Full license can be found in the top level "LICENSE" file.
"""Internal helper functions for unit tests.
"""

import os
import tempfile
import shutil

import numpy as np

from astropy import units as u

from sotodlib.sim_hardware import get_example

from sotodlib.sim_hardware import sim_telescope_detectors

toast_available = None
if toast_available is None:
    try:
        import toast
        import toast.ops
        from toast.observation import default_values as defaults
        from toast import schedule_sim_ground
        import sotodlib.toast as sotoast

        toast_available = True
    except ImportError:
        toast_available = False


def create_outdir(subdir=None, mpicomm=None):
    """Create the top level output directory and per-test subdir.

    Args:
        subdir (str): the sub directory for this test.

    Returns:
        str: full path to the test subdir if specified, else the top dir.

    """
    pwd = os.path.abspath(".")
    testdir = os.path.join(pwd, "sotodlib_test_output")
    retdir = testdir
    if subdir is not None:
        retdir = os.path.join(testdir, subdir)
    if (mpicomm is None) or (mpicomm.rank == 0):
        if not os.path.isdir(testdir):
            os.mkdir(testdir)
        if not os.path.isdir(retdir):
            os.mkdir(retdir)
    if mpicomm is not None:
        mpicomm.barrier()
    return retdir


def create_comm(mpicomm):
    """Create a toast communicator.

    Use the specified MPI communicator to attempt to create 2 process groups.
    If less than 2 processes are used, create a single process group.

    Args:
        mpicomm (MPI.Comm): the MPI communicator (or None).

    Returns:
        toast.Comm: the 2-level toast communicator.

    """
    if not toast_available:
        raise RuntimeError("TOAST is not importable, cannot create a toast.Comm")
    toastcomm = None
    if mpicomm is None:
        toastcomm = toast.Comm(world=mpicomm)
    else:
        worldsize = mpicomm.size
        groupsize = 1
        if worldsize >= 2:
            groupsize = worldsize // 2
        toastcomm = toast.Comm(world=mpicomm, groupsize=groupsize)
    return toastcomm


def simulation_test_data(
    mpicomm,
    telescope_name="SAT4",
    wafer_slot="w42",
    band="SAT_f030",
    sample_rate=10.0 * u.Hz,
    temp_dir=None,
    el_nod=False,
    el_nods=[-1 * u.degree, 1 * u.degree],
    thin_fp=4,
):
    """Create a data object with a simple ground sim.

    Use the specified MPI communicator to attempt to create 2 process groups.  Create
    a fake telescope and run the ground sim to make some observations for each
    group.  This is useful for testing many operators that need some pre-existing
    observations with boresight pointing.

    Args:
        mpicomm (MPI.Comm): the MPI communicator (or None).
        sample_rate (Quantity): the sample rate.

    Returns:
        toast.Data: the distributed data with named observations.

    """
    if not toast_available:
        raise RuntimeError("TOAST is not importable, cannot simulate test data")

    # Create the communicator
    toastcomm = create_comm(mpicomm)

    # Focalplane
    fp = sotoast.SOFocalplane(
        telescope=telescope_name,
        sample_rate=sample_rate,
        bands=band,
        wafer_slots=wafer_slot,
        thinfp=thin_fp,
        comm=mpicomm,
    )

    # Telescope
    site = toast.instrument.GroundSite(
        "atacama", "-22:57:30", "-67:47:10", 5200.0 * u.meter
    )
    telescope = toast.instrument.Telescope(telescope_name, focalplane=fp, site=site)

    data = toast.Data(toastcomm)

    # Create a schedule.

    # FIXME: change this once the ground scheduler supports in-memory creation of the
    # schedule.

    schedule = None

    if mpicomm is None or mpicomm.rank == 0:
        tdir = temp_dir
        if tdir is None:
            tdir = tempfile.mkdtemp()

        sch_file = os.path.join(tdir, "ground_schedule.txt")
        schedule_sim_ground.run_scheduler(
            opts=[
                "--site-name",
                telescope.site.name,
                "--telescope",
                telescope.name,
                "--site-lon",
                "{}".format(telescope.site.earthloc.lon.to_value(u.degree)),
                "--site-lat",
                "{}".format(telescope.site.earthloc.lat.to_value(u.degree)),
                "--site-alt",
                "{}".format(telescope.site.earthloc.height.to_value(u.meter)),
                "--patch",
                "small_patch,1,40,-40,44,-44",
                "--start",
                "2020-01-01 00:00:00",
                "--stop",
                "2020-01-01 06:00:00",
                "--out",
                sch_file,
            ]
        )
        schedule = toast.schedule.GroundSchedule()
        schedule.read(sch_file)
        if temp_dir is None:
            shutil.rmtree(tdir)
    if mpicomm is not None:
        schedule = mpicomm.bcast(schedule, root=0)

    sim_ground = toast.ops.SimGround(
        name="sim_ground",
        telescope=telescope,
        schedule=schedule,
        hwp_angle=defaults.hwp_angle,
        hwp_rpm=120.0,
        weather="atacama",
        detset_key="pixel",
        elnod_start=el_nod,
        elnods=el_nods,
        det_flags="flags",
        det_data="signal",
        shared_flags="flags",
        scan_accel_az=3 * u.degree / u.second ** 2,
    )
    sim_ground.apply(data)

    return data
