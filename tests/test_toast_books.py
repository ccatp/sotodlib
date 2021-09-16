# Copyright (c) 2018-2021 Simons Observatory.
# Full license can be found in the top level "LICENSE" file.
"""Test toast data export.
"""

import os
import shutil

import numpy as np

from unittest import TestCase

# Import so3g before any other packages that import spt3g
import so3g

from ._helpers import create_outdir, simulation_test_data

from sotodlib.sim_hardware import get_example

from sotodlib.sim_hardware import sim_telescope_detectors

toast_available = None
if toast_available is None:
    try:
        import toast
        from toast.mpi import get_world
        import toast.ops
        from toast.observation import default_values as defaults
        import sotodlib.toast as sotoast
        from sotodlib.toast import ops as sops
        from sotodlib.toast import io as stio

        toast_available = True
    except ImportError:
        raise
        #toast_available = False


class ToastBooksTest(TestCase):
    def setUp(self):
        fixture_name = os.path.splitext(os.path.basename(__file__))[0]
        if not toast_available:
            print("TOAST cannot be imported- skipping unit tests", flush=True)
            return
        world, procs, rank = get_world()
        self.outdir = create_outdir(fixture_name, mpicomm=world)
        self.framefile_mb = 200.0

    def test_book_save(self):
        if not toast_available:
            return
        world, procs, rank = get_world()
        testdir = os.path.join(self.outdir, "books")
        if world is None or world.rank == 0:
            if os.path.isdir(testdir):
                shutil.rmtree(testdir)
            os.makedirs(testdir)
        if world is not None:
            world.barrier()

        data = simulation_test_data(world)

        # Create a noise model from focalplane detector properties
        noise_model = toast.ops.DefaultNoiseModel()
        noise_model.apply(data)

        # Simulate some noise
        sim_noise = toast.ops.SimNoise()
        sim_noise.apply(data)

        # Set up the save operator

        save = sops.SaveBooks()

        # Save the data
        save.apply(data)

        # meta_exporter = stio.save_bookbinder_obs_meta()
        # data_exporter = stio.save_bookbinder_obs_data(
        #     timestamp_names=(defaults.times, defaults.times),
        #     shared_names=[
        #         (defaults.shared_flags, "shared_flags", None),
        #         (defaults.hwp_angle, defaults.hwp_angle, None),
        #         (defaults.azimuth, defaults.azimuth, None),
        #         (defaults.elevation, defaults.elevation, None),
        #         (defaults.boresight_azel, defaults.boresight_azel, None),
        #         (defaults.boresight_radec, defaults.boresight_radec, None),
        #         (defaults.position, defaults.position, None),
        #         (defaults.velocity, defaults.velocity, None),
        #     ],
        #     det_names=[
        #         (defaults.det_data, defaults.det_data, np.float64, {"enable": 0}),
        #         (defaults.det_flags, defaults.det_flags, np.int32, {"enable": 0}),
        #     ],
        #     interval_names=[
        #         ("scan_leftright", "intervals_scan_leftright"),
        #         ("turn_leftright", "intervals_turn_leftright"),
        #         ("scan_rightleft", "intervals_scan_rightleft"),
        #         ("turn_rightleft", "intervals_turn_rightleft"),
        #         ("elnod", "intervals_elnod"),
        #         ("scanning", "intervals_scanning"),
        #         ("turnaround", "intervals_turnaround"),
        #         ("sun_up", "intervals_sun_up"),
        #         ("sun_close", "intervals_sun_close"),
        #     ],
        # )

        # exporter = toast.spt3g.export_obs(
        #     meta_export=meta_exporter,
        #     data_export=data_exporter,
        # )

        # # Export the data, and make a copy for later comparison.
        # original = list()
        # g3data = list()
        # for ob in data.obs:
        #     original.append(ob.duplicate(times="times"))
        #     ob_dir = os.path.join(testdir, ob.name)
        #     if data.comm.group_rank == 0:
        #         if os.path.isdir(ob_dir):
        #             shutil.rmtree(ob_dir)
        #         os.makedirs(ob_dir)
        #     if data.comm.comm_group is not None:
        #         data.comm.comm_group.barrier()
        #     meta_exporter.out_dir = ob_dir

        #     obframes = exporter(ob)

        #     emitter = toast.spt3g.frame_emitter(frames=obframes)

        #     save_pipe = c3g.G3Pipeline()
        #     save_pipe.Add(emitter)
        #     save_pipe.Add(
        #         c3g.G3MultiFileWriter,
        #         filename=os.path.join(ob_dir, "frames-%05u.g3"),
        #         size_limit=int(self.framefile_mb * 1024 ** 2),
        #     )
        #     save_pipe.Run()

        #     del save_pipe
        #     del emitter

            # There should be the original number of frame intervals plus
            # one observation frame
            #g3data.append(obframes)

        # # Import the data
        # check_data = Data(comm=data.comm)

        # for obframes in g3data:
        #     check_data.obs.append(importer(obframes))

        # for ob in check_data.obs:
        #     ob.redistribute(ob.comm.group_size, times="times")

        # # Verify
        # for ob, orig in zip(check_data.obs, original):
        #     if ob != orig:
        #         print(f"-------- Proc {data.comm.world_rank} ---------\n{orig}\n{ob}")
        #     self.assertTrue(ob == orig)
