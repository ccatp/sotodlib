# Copyright (c) 2022-2022 Simons Observatory.
# Full license can be found in the top level "LICENSE" file.

import os

import numpy as np

import traitlets

from astropy import units as u

from toast.timing import function_timer

from toast.traits import trait_docs, Int, Unicode, Bool, Quantity, Float, Instance

from toast.ops.operator import Operator

from toast.utils import Environment, Logger, Timer

from toast.observation import default_values as defaults

from ..io import book_name_from_obs, export_obs_meta, export_obs_ancil, export_obs_data

from ...sim_hardware import telescope_tube_wafer


@trait_docs
class SaveBooks(Operator):
    """Export observations to Level-3 Book format.

    Create one book per observation.  Basic metadata is written to Observation
    frames at the start of each Primary File Group.  Other information is written
    to external files nData that is not part of the raw
    data is written to "Z-prefixed" files in the book directory.  These
    include the focalplane model as an astropy table ECSV file, and the
    noise model if specified (as an HDF5 file).

    """

    # Class traits

    API = Int(0, help="Internal interface version for this operator")

    directory = Unicode("books", help="Top-level export directory")

    times = Unicode(defaults.times, help="Observation shared key for timestamps")

    boresight_azel = Unicode(
        defaults.boresight_azel, help="Observation shared key for boresight Az/El"
    )

    corotator_angle = Unicode(
        "corotator_angle",
        allow_none=True,
        help="Observation shared key for corotator_angle",
    )

    boresight_angle = Unicode(
        None,
        allow_none=True,
        help="Observation shared key for boresight rotation angle",
    )

    hwp_angle = Unicode(defaults.hwp_angle, help="Observation shared key for HWP angle")

    det_data = Unicode(
        defaults.det_data,
        help="Observation detdata key for simulated signal",
    )

    frame_intervals = Unicode(
        None,
        allow_none=True,
        help="Observation interval key for frame boundaries",
    )

    gzip = Bool(False, help="If True, gzip compress the frame files")

    purge = Bool(False, help="If True, delete observation data as it is saved")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @function_timer
    def _exec(self, data, detectors=None, **kwargs):
        log = Logger.get()
        timer = Timer()
        timer.start()

        for ob in data.obs:
            book_name = book_name_from_obs(ob, times=self.times)
            book_dir = os.path.join(self.directory, book_name)
            print(f"Saving observation {ob.name} to {book_dir}")

            # Create frame intervals if not specified
            redist_sampsets = False
            frame_intervals = self.frame_intervals
            if frame_intervals is None:
                # We are using the sample set distribution for our frame boundaries.
                frame_intervals = "frames"
                timespans = list()
                offset = 0
                n_frames = 0
                first_set = ob.dist.samp_sets[ob.comm.group_rank].offset
                n_set = ob.dist.samp_sets[ob.comm.group_rank].n_elem
                for sset in range(first_set, first_set + n_set):
                    for chunk in ob.dist.sample_sets[sset]:
                        timespans.append(
                            (
                                ob.shared[self.times][offset],
                                ob.shared[self.times][offset + chunk - 1],
                            )
                        )
                        n_frames += 1
                        offset += chunk
                ob.intervals.create_col(frame_intervals, timespans, ob.shared[self.times])
            else:
                # We were given an existing set of frame boundaries.  Compute new
                # sample sets to use when redistributing.
                if ob.comm_col_rank == 0:
                    # First row of process grid gets local chunks
                    local_sets = list()
                    offset = 0
                    for intr in ob.intervals[frame_intervals]:
                        chunk = intr.last - offset + 1
                        local_sets.append([chunk,])
                        offset += chunk
                    if offset != ob.n_local_samples:
                        local_sets.append([ob.n_local_samples - offset])
                    # Gather across the row
                    all_sets = [local_sets,]
                    if ob.comm_row is not None:
                        all_sets = ob.comm_row.gather(local_sets, root=0)
                    if ob.comm_row_rank == 0:
                        redist_sampsets = list()
                        for pset in all_sets:
                            redist_sampsets.extend(pset)
                if ob.comm.comm_group is not None:
                    redist_sampsets = ob.comm.comm_group.bcast(redist_sampsets, root=0)

            # Redistribute observation in memory to be in slices of times
            ob.redistribute(
                1,
                times=self.times,
                override_sample_sets=redist_sampsets,
            )

            # Export common ancillary data
            aframes = export_obs_ancil(
                ob,
                self.times,
                self.boresight_azel,
                self.corotator_angle,
                self.boresight_angle,
                self.hwp_angle,
                frame_intervals
            )
            # print(aframes)

            wafers = set(ob.telescope.focalplane.detector_data[:]["wafer_slot"])
            for wf in wafers:
                obframe, fp_raw, fp_derived = export_obs_meta(ob, wf)

                dframes = export_obs_data(
                    ob,
                    wf,
                    self.times,
                    self.det_data,
                    frame_intervals
                )

                # Add ancil frame data to data frames



                # print(obframe)
                # print(fp_raw)
                # print(fp_derived)

            # Delete our temporary frame interval if we created it
            if self.frame_intervals is None:
                del ob.intervals[frame_intervals]

        #     # If the export rank is set, then frames will be gathered to one
        #     # process and written.  Otherwise each process will write to
        #     # sequential, independent frame files.
        #     ex_rank = self.obs_export.export_rank
        #     if ex_rank is None:
        #         # All processes write independently
        #         emitter = frame_emitter(frames=frames)
        #         save_pipe = c3g.G3Pipeline()
        #         save_pipe.Add(emitter)
        #         fname = f"frames-{ob.comm.group_rank:04d}.g3"
        #         if self.gzip:
        #             fname += ".gz"
        #         save_pipe.Add(
        #             c3g.G3Writer,
        #             filename=os.path.join(ob_dir, fname),
        #         )
        #         save_pipe.Run()
        #         del save_pipe
        #         del emitter
        #     else:
        #         # Gather frames to one process and write
        #         if ob.comm.group_rank == ex_rank:
        #             emitter = frame_emitter(frames=frames)
        #             save_pipe = c3g.G3Pipeline()
        #             save_pipe.Add(emitter)
        #             fpattern = "frames-%04u.g3"
        #             if self.gzip:
        #                 fpattern += ".gz"
        #             save_pipe.Add(
        #                 c3g.G3MultiFileWriter,
        #                 filename=os.path.join(ob_dir, fpattern),
        #                 size_limit=int(self.framefile_mb * 1024 ** 2),
        #             )
        #             save_pipe.Run()
        #             del save_pipe
        #             del emitter

        #     if ob.comm.comm_group is not None:
        #         ob.comm.comm_group.barrier()

        #     if self.purge:
        #         ob.clear()

        # if self.purge:
        #     data.obs.clear()

    def _finalize(self, data, **kwargs):
        return

    def _requires(self):
        req = {
            "shared": [
                self.times,
                self.boresight_azel,
            ],
            "detdata": [self.det_data],
        }
        if self.boresight_angle is not None:
            req["shared"].append(self.boresight_angle)
        if self.corotator_angle is not None:
            req["shared"].append(self.corotator_angle)
        if self.hwp_angle is not None:
            req["shared"].append(self.hwp_angle)
        return req

    def _provides(self):
        return dict()
