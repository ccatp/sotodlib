# Copyright (c) 2020-2021 Simons Observatory.
# Full license can be found in the top level "LICENSE" file.
"""Tools for saving Level-3 Book format data.

"""

import os
import sys

import numpy as np

from astropy import units as u

from astropy.table import Table

import h5py

# Import so3g before any other packages that import spt3g
import so3g
from spt3g import core as c3g

import toast
import toast.spt3g as t3g
import toast.qarray as qa

from toast.observation import default_values as defaults

from ...sim_hardware import telescope_tube_wafer

from ...__init__ import __version__ as sotodlib_version


def book_name_from_obs(obs, times=defaults.times):
    """Return the name of the book, given an observation.

    This uses focalplane information to build the name of the book directory.

    Args:
        obs (Observation):  The observation.
        times (str):  Override the shared field to use for timestamps.

    Returns:
        (str):  The book name.

    """
    log = toast.utils.Logger.get()
    timestamp = int(obs.shared[times][0])

    focalplane = obs.telescope.focalplane
    tele_name = focalplane.telescope

    props = focalplane.detector_data

    tube_check = set(props[:]["tube_slot"])
    if len(tube_check) != 1:
        msg = f"Focalplane has multiple tube_slots ({tube_check}), cannot save"
        raise RuntimeError(msg)
    tube_name = tube_check.pop()

    if tele_name == "LAT":
        tele_tube = f"lat{tube_name}"
    else:
        tele_tube = tele_name

    wafer_set = set(props[:]["wafer_slot"])

    wafer_bits = ""
    ttw = telescope_tube_wafer()
    for wf in ttw[tele_name][tube_name]:
        if wf in wafer_set:
            wafer_bits = f"{wafer_bits}1"
        else:
            wafer_bits = f"{wafer_bits}0"

    return f"obs_{timestamp:10d}_{tele_tube}_{wafer_bits}"


def export_obs_meta(obs, wafer_slot):
    """Extract Observation metadata for a wafer slot.

    For given wafer return general metadata as Observation frame.  Return "known"
    meta data (at time of acquisition) in one table indexed by channel index.
    Return "derived" focalplane data as a separate table.

    Args:
        obs (Observation):  The observation.
        wafer_slot (str):  The wafer slot to export.

    Returns:
        (tuple):  The observation frame, channel table, and focalplane table.

    """
    log = toast.utils.Logger.get()
    log.verbose(f"Create observation frame for {obs.name}")
    # Construct observation frame
    ob = c3g.G3Frame(c3g.G3FrameType.Observation)
    ob["observation_name"] = c3g.G3String(obs.name)
    ob["observation_uid"] = c3g.G3Int(obs.uid)
    ob["telescope_name"] = c3g.G3String(obs.telescope.name)
    ob["telescope_class"] = c3g.G3String(
        toast.utils.object_fullname(obs.telescope.__class__)
    )
    ob["telescope_uid"] = c3g.G3Int(obs.telescope.uid)
    site = obs.telescope.site
    ob["site_name"] = c3g.G3String(site.name)
    ob["site_class"] = c3g.G3String(toast.utils.object_fullname(site.__class__))
    ob["site_uid"] = c3g.G3Int(site.uid)
    if isinstance(site, toast.instrument.GroundSite):
        ob["site_lat_deg"] = c3g.G3Double(site.earthloc.lat.to_value(u.degree))
        ob["site_lon_deg"] = c3g.G3Double(site.earthloc.lon.to_value(u.degree))
        ob["site_alt_m"] = c3g.G3Double(site.earthloc.height.to_value(u.meter))
        if site.weather is not None:
            if hasattr(site.weather, "name"):
                # This is a simulated weather object, dump it.
                ob["site_weather_name"] = c3g.G3String(site.weather.name)
                ob["site_weather_realization"] = c3g.G3Int(site.weather.realization)
                if site.weather.max_pwv is None:
                    ob["site_weather_max_pwv"] = c3g.G3String("NONE")
                else:
                    ob["site_weather_max_pwv"] = c3g.G3Double(site.weather.max_pwv)
                ob["site_weather_time"] = t3g.to_g3_time(site.weather.time.timestamp())

    # Select rows of our focalplane that are from the given wafer_slot.
    # Order must be preserved.
    fp_data = obs.telescope.focalplane.detector_data
    det_rows = fp_data["wafer_slot"] == wafer_slot
    if np.sum(det_rows) == 0:
        msg = f"observation {ob.name} has no dets with wafer_slot {wafer_slot}"
        log.error(msg)
        raise RuntimeError(msg)

    raw_cols = [
        "channel",
        "card_slot",
        "AMC",
        "bias",
        "readout_freq",
        "bondpad",
        "mux_position",
    ]

    orig_cols = list(fp_data.colnames)
    fp_cols = ["channel"]
    for col in orig_cols:
        if col not in raw_cols:
            fp_cols.append(col)

    chaninfo = fp_data[raw_cols][det_rows]
    derived = fp_data[fp_cols][det_rows]

    readout_id as <wafer>_<sim start time>_<channel // 8>_<channel % 8>

    return ob, chaninfo, derived


def export_obs_ancil(
    obs,
    times,
    boresight_azel,
    corotator_angle,
    boresight_angle,
    hwp_angle,
    frame_intervals,
):
    """Extract Observation ancillary data.

    This extracts the shared data fields.

    Args:
        obs (Observation):  The observation.
        frame_intervals (str):  The name of the intervals to use for frame boundaries.
            If not specified, the observation sample sets are used.

    Returns:
        (list):  The frames.

    """
    log = toast.utils.Logger.get()
    log.verbose(f"Create ancillary frames for {obs.name}")

    book_id = book_name_from_obs(obs, times=times)

    output = list()
    frame_view = obs.view[frame_intervals]
    for ivw, tview in enumerate(frame_view.shared[times]):
        # Construct the Scan frame
        frame = c3g.G3Frame(c3g.G3FrameType.Scan)
        frame["book_id"] = c3g.G3String(book_id)
        frame["sample_range"] = c3g.G3VectorInt(
            np.array(
                [
                    obs.intervals[frame_intervals][ivw].first,
                    obs.intervals[frame_intervals][ivw].last + 1,
                ],
                dtype=np.int64
            )
        )

        ancil = c3g.G3TimesampleMap()
        ancil.times = t3g.to_g3_time(tview)

        theta, phi, pa = qa.to_angles(frame_view.shared[boresight_azel][ivw])
        ancil["az_enc"] = c3g.G3VectorDouble(-phi)
        ancil["el_enc"] = c3g.G3VectorDouble((np.pi / 2) - theta)

        # FIXME:  boresight / corotation should be extracted from obs metadata

        if hwp_angle is not None:
            ancil["hwp_enc"] = c3g.G3VectorDouble(frame_view.shared[hwp_angle][ivw])

        frame["ancil"] = ancil
        output.append(frame)

    return output


def export_obs_data(
    obs,
    wafer_slot,
    times,
    det_data,
    frame_intervals,
):
    """Extract Observation detector data.

    Args:
        obs (Observation):  The observation.
        frame_intervals (str):  The name of the intervals to use for frame boundaries.
            If not specified, the observation sample sets are used.

    Returns:
        (list):  The frames.

    """
    log = toast.utils.Logger.get()
    log.verbose(f"Create data frames for {obs.name}")

    fp_data = obs.telescope.focalplane.detector_data
    det_rows = np.array(fp_data["wafer_slot"] == wafer_slot, dtype=np.int32)
    print("det_rows = ", det_rows, flush=True)

    det_names = list(obs.local_detectors)[det_rows]

    output = list()
    frame_view = obs.view[frame_intervals]
    for ivw, dview in enumerate(frame_view.detdata[det_data]):
        # Construct the Scan frame
        frame = c3g.G3Frame(c3g.G3FrameType.Scan)
        frame["stream_id"] = c3g.G3String(stream_id)

        ts = so3g.G3SuperTimestream()
        ts.names = det_names
        ts.times = t3g.to_g3_time(frame_view.shared[times][ivw])
        ts.quanta = np.ones(len(det_names))
        ts.data = frame_view.detdata[det_data][ivw][det_rows]

        frame["signal"] = ts

        output.append(frame)

    return output


@toast.timing.function_timer
def save_book_shared(obs, name, view_name=None, view_index=0, g3t=None):
    """Convert a single shared object to a G3Object.

    If the G3 data type is not specified, a guess will be made at the closest
    appropriate type.

    Args:
        obs (Observation):  The parent observation.
        name (str):  The name of the shared object.
        view_name (str):  If specified, use this view of the shared object.
        view_index (int):  Export this element of the list of data views.
        g3t (G3Object):  The specific G3Object type to use, or None.

    Returns:
        (G3Object):  The resulting G3 object.

    """
    if name not in obs.shared:
        raise KeyError(f"Shared object '{name}' does not exist in observation")
    if g3t is None:
        g3t = t3g.to_g3_array_type(obs.shared[name].dtype)

    sview = obs.shared[name].data
    if view_name is not None:
        sview = np.array(obs.view[view_name].shared[name][view_index], copy=False)

    if g3t == c3g.G3VectorTime:
        return t3g.to_g3_time(sview)
    elif g3t == c3g.G3VectorQuat:
        return t3g.to_g3_quats(sview)
    else:
        return g3t(sview.flatten().tolist())


@toast.timing.function_timer
def save_book_detdata(
    obs, name, view_name=None, view_index=0, dtype=None, times=None, options=None
):
    """Convert a single detdata object to a G3SuperTimestream.

    If the output dtype is not specified, the best type will be chosen based on the
    existing detdata dtype.

    Args:
        obs (Observation):  The parent observation.
        name (str):  The name of the detdata object.
        view_name (str):  If specified, use this view of the detdata object.
        view_index (int):  Export this element of the list of data views.
        dtype (numpy.dtype):  Override the output dtype.
        times (str):  Use this shared name for the timestamps.
        options (dict):  If not None, these will be passed to the G3SuperTimestream
            options() method.

    Returns:
        (G3SuperTimestream, G3Units):  The resulting G3 object and the units.

    """
    if name not in obs.detdata:
        raise KeyError(f"DetectorData object '{name}' does not exist in observation")

    # Find the G3 equivalent units and scale factor needed to get the data into that
    gunit, scale = t3g.to_g3_unit(obs.detdata[name].units)

    # Find the best supported dtype
    if dtype is None:
        ch = obs.detdata[name].dtype.char
        if ch == "f":
            dtype = np.float32
        elif ch == "d":
            dtype = np.float64
        elif ch == "l" or ch == "L":
            dtype = np.int64
        elif ch in ["i", "I", "h", "H", "b", "B"]:
            dtype = np.int32
        else:
            raise RuntimeError(f"Unsupported timestream data type '{ch}'")

    # Get the view of the data, either the whole observation or one interval
    dview = obs.detdata[name]
    tview = obs.shared[times]
    if view_name is not None:
        dview = obs.view[view_name].detdata[name][view_index]
        tview = np.array(obs.view[view_name].shared[times][view_index], copy=False)

    out = so3g.G3SuperTimestream()
    out.names = dview.detectors
    out.times = t3g.to_g3_time(tview)
    # if dtype == np.float32 or dtype == np.float64:
    out.quanta = np.ones(len(dview.detectors))
    out.data = scale * dview[:].astype(dtype)

    # Set any options
    if options is not None:
        out.options(**options)

    return out, gunit


@toast.timing.function_timer
def save_book_intervals(obs, name, iframe):
    """Convert the named intervals into a G3 object.

    Args:
        obs (Observation):  The parent observation.
        name (str):  The name of the intervals.
        iframe (IntervalList):  An interval list defined for this frame.

    Returns:
        (G3Object):  An IntervalsTime object.

    """
    overlap = iframe & obs.intervals[name]

    out = None
    try:
        out = c3g.IntervalsTime(
            [(t3g.o_g3_time(x.start), t3g.to_g3_time(x.stop)) for x in overlap]
        )
    except Exception:
        # Intervals objects not available
        out = c3g.G3VectorTime(
            [
                elem
                for x in overlap
                for elem in (t3g.to_g3_time(x.start), t3g.to_g3_time(x.stop))
            ]
        )

    return out


class save_book_obs_meta(object):
    """Default class to export Observation metadata.

    In the bookbinder format we have G3 data that consists of a simple observation
    frame and a stream of Scan frames.  The scan frames have detector data indexed
    by readout channel.  The other detector properties and mapping from from detector
    to readout are contained in an HDF5 file that is located in the same directory
    as the frame files for an observation.

    Args:
        out_dir (str):  The output directory.
        meta_file (str):  The base filename to write

    """

    def __init__(self, out_dir=None, meta_file="metadata.h5"):
        self.out_dir = out_dir
        self._meta_file = meta_file

    @toast.timing.function_timer
    def __call__(self, obs):
        log = toast.utils.Logger.get()
        log.verbose(f"Create observation frame and HDF5 file for {obs.name} in {dir}")

        # Construct observation frame
        ob = self._create_obs_frame(obs)

        # Write hdf5 file
        self._create_meta_file(obs, os.path.join(self.out_dir, self._meta_file))

        return ob, c3g.G3Frame(c3g.G3FrameType.Calibration)

    def _create_obs_frame(self, obs):
        # Construct observation frame
        ob = c3g.G3Frame(c3g.G3FrameType.Observation)
        ob["observation_name"] = c3g.G3String(obs.name)
        ob["observation_uid"] = c3g.G3Int(obs.uid)
        ob["observation_n_channels"] = c3g.G3Int(len(obs.all_detectors))
        ob["observation_n_samples"] = c3g.G3Int(obs.n_all_samples)
        ob["telescope_name"] = c3g.G3String(obs.telescope.name)
        ob["telescope_class"] = c3g.G3String(
            toast.utils.object_fullname(obs.telescope.__class__)
        )
        ob["telescope_uid"] = c3g.G3Int(obs.telescope.uid)
        site = obs.telescope.site
        ob["site_name"] = c3g.G3String(site.name)
        ob["site_class"] = c3g.G3String(toast.utils.object_fullname(site.__class__))
        ob["site_uid"] = c3g.G3Int(site.uid)
        ob["site_lat_deg"] = c3g.G3Double(site.earthloc.lat.to_value(u.degree))
        ob["site_lon_deg"] = c3g.G3Double(site.earthloc.lon.to_value(u.degree))
        ob["site_alt_m"] = c3g.G3Double(site.earthloc.height.to_value(u.meter))

        # Export whatever other metadata we can.  Not all information can be
        # easily stored in a frame, so the HDF5 file will have the full set.
        for m_key, m_val in obs.items():
            try:
                l = len(m_val)
                # This is an array
                ob[m_key] = t3g.to_g3_array_type(m_val)
            except Exception:
                # This is a scalar (no len defined)
                try:
                    ob[m_key], m_unit = t3g.to_g3_scalar_type(m_val)
                    if m_unit is not None:
                        ob[f"{m_key}_astropy_units"] = c3g.G3String(f"{m_val.unit}")
                        ob[f"{m_key}_units"] = m_unit
                except Exception:
                    # This is not a datatype we can convert
                    pass
        return ob

    def _create_meta_file(self, obs, path):
        log = toast.utils.Logger.get()
        if os.path.isfile(path):
            raise RuntimeError(f"Metadata file '{path}' already exists")

        path_temp = f"{path}.tmp"
        if os.path.isfile(path_temp):
            os.remove(path_temp)
        with h5py.File(path_temp, "w") as hf:
            # Record the software versions and config
            hf.attrs["software_version_so3g"] = so3g.__version__
            hf.attrs["software_version_sotodlib"] = sotodlib_version
            toast_env = toast.Environment.get()
            hf.attrs["software_version_toast"] = toast_env.version()

            # Observation properties
            hf.attrs["observation_name"] = obs.name
            hf.attrs["observation_uid"] = obs.uid
            hf.attrs["observation_n_channels"] = len(obs.all_detectors)
            hf.attrs["observation_n_samples"] = obs.n_all_samples
            # FIXME:  what other information would be useful at the top
            # level?  Maybe start time?

            # Instrument properties
            inst_group = hf.create_group("instrument")
            inst_group.attrs["telescope_name"] = obs.telescope.name
            inst_group.attrs["telescope_class"] = toast.utils.object_fullname(
                obs.telescope.__class__
            )
            inst_group.attrs["telescope_uid"] = obs.telescope.uid
            site = obs.telescope.site
            inst_group.attrs["site_name"] = site.name
            inst_group.attrs["site_class"] = toast.utils.object_fullname(site.__class__)
            inst_group.attrs["site_uid"] = site.uid
            inst_group.attrs["site_lat_deg"] = site.earthloc.lat.to_value(u.degree)
            inst_group.attrs["site_lon_deg"] = site.earthloc.lon.to_value(u.degree)
            inst_group.attrs["site_alt_m"] = site.earthloc.height.to_value(u.meter)

            obs.telescope.focalplane.save_hdf5(inst_group, comm=None, force_serial=True)
            del inst_group

            # Track metadata that has already been dumped
            meta_done = set()

            # Dump additional simulation data such as noise models, weather model, etc
            # to a separate group.
            sim_group = hf.create_group("simulation")
            if site.weather is not None:
                if hasattr(site.weather, "name"):
                    # This is a simulated weather object, dump it.
                    sim_group.attrs["site_weather_name"] = str(site.weather.name)
                    sim_group.attrs[
                        "site_weather_realization"
                    ] = site.weather.realization
                    if site.weather.max_pwv is None:
                        sim_group.attrs["site_weather_max_pwv"] = "NONE"
                    else:
                        sim_group.attrs["site_weather_max_pwv"] = site.weather.max_pwv
                    sim_group.attrs["site_weather_time"] = site.weather.time.timestamp()
            for k, v in obs.items():
                if isinstance(v, toast.noise.Noise):
                    kgroup = sim_group.create_group(k)
                    kgroup.attrs["class"] = toast.utils.object_fullname(v.__class__)
                    v.save_hdf5(kgroup, comm=None, force_serial=True)
                    del kgroup
                    meta_done.add(k)
            del sim_group

            # Other arbitrary metadata
            meta_group = hf.create_group("metadata")
            for k, v in obs.items():
                if k in meta_done:
                    continue
                if hasattr(v, "save_hdf5"):
                    kgroup = meta_group.create_group(k)
                    kgroup.attrs["class"] = toast.utils.object_fullname(v.__class__)
                    v.save_hdf5(kgroup, comm=None, force_serial=True)
                    del kgroup
                elif isinstance(v, u.Quantity):
                    if isinstance(v.value, np.ndarray):
                        # Array quantity
                        qdata = meta_group.create_dataset(k, data=v.value)
                        qdata.attrs["units"] = v.unit.to_string()
                        del qdata
                    else:
                        # Must be a scalar
                        meta_group.attrs[f"{k}"] = v.value
                        meta_group.attrs[f"{k}_units"] = v.unit.to_string()
                elif isinstance(v, np.ndarray):
                    marr = meta_group.create_dataset(k, data=v)
                    del marr
                else:
                    try:
                        meta_group.attrs[k] = v
                    except ValueError as e:
                        msg = f"Failed to store obs key '{k}' = '{v}' as an attribute "
                        msg += f"({e}).  Try casting it to a supported type when "
                        msg += f"storing in the observation dictionary or implement "
                        msg += f"save_hdf5() and load_hdf5() methods."
                        log.warning(msg)
            del meta_group

        # Move the file into place
        os.rename(path_temp, path)


class save_book_obs_data(object):
    """Class to export Scan frames.

    Shared objects:  The `shared_names` list of tuples specifies the TOAST shared key,
    corresponding Scan frame key, and optionally the G3 datatype to use.  Each process
    will duplicate shared data into their Scan frame stream.  If the G3 datatype is
    None, the closest G3 object type will be chosen.  If the shared object contains
    multiple values per sample, these are reshaped into a flat-packed array.  Only
    sample-wise shared objects are supported at this time (i.e. no other shared objects
    like beams, etc).  One special case:  The `timestamps` field will always be copied
    to each Scan frame as a `G3Timestream`.

    DetData objects:  The `det_names` list of tuples specifies the TOAST detdata key,
    the corresponding Scan frame key, and optionally the numpy dtype and compression
    options.  The data is exported to G3SuperTimestream objects.

    Intervals objects:  The `interval_names` list of tuples specifies the TOAST
    interval name and associated Scan frame key.  We save these to `IntervalsTime`
    objects filled with the start / stop times of each interval.

    Args:
        timestamp_names (tuple):  The name of the shared data containing the
            timestamps, and the output frame key to use.
        frame_intervals (str):  The name of the intervals to use for frame boundaries.
            If not specified, the observation sample sets are used.
        shared_names (list):  The observation shared objects to export.
        det_names (list):  The observation detdata objects to export.
        interval_names (list):  The observation intervals to export.

    """

    def __init__(
        self,
        timestamp_names=("times", "times"),
        frame_intervals=None,
        shared_names=list(),
        det_names=list(),
        interval_names=list(),
    ):
        self._timestamp_names = timestamp_names
        self._frame_intervals = frame_intervals
        self._shared_names = shared_names
        self._det_names = det_names
        self._interval_names = interval_names

    @toast.timing.function_timer
    def __call__(self, obs):
        log = toast.utils.Logger.get()
        frame_intervals = self._frame_intervals
        if frame_intervals is None:
            # We are using the sample set distribution for our frame boundaries.
            frame_intervals = "frames"
            timespans = list()
            offset = 0
            n_frames = 0
            first_set = obs.dist.samp_sets[obs.comm.group_rank].offset
            n_set = obs.dist.samp_sets[obs.comm.group_rank].n_elem
            for sset in range(first_set, first_set + n_set):
                for chunk in obs.dist.sample_sets[sset]:
                    timespans.append(
                        (
                            obs.shared[self._timestamp_names[0]][offset],
                            obs.shared[self._timestamp_names[0]][offset + chunk - 1],
                        )
                    )
                    n_frames += 1
                    offset += chunk
            obs.intervals.create_col(
                frame_intervals, timespans, obs.shared[self._timestamp_names[0]]
            )

        output = list()
        frame_view = obs.view[frame_intervals]
        for ivw, tview in enumerate(frame_view.shared[self._timestamp_names[0]]):
            msg = f"Create scan frame {obs.name}:{ivw} with fields:"
            msg += f"\n  shared:  {self._timestamp_names[1]}"
            nms = ", ".join([y for x, y, z in self._shared_names])
            msg += f", {nms}"
            nms = ", ".join([x for w, x, y, z in self._det_names])
            msg += f"\n  detdata:  {nms}"
            nms = ", ".join([y for x, y in self._interval_names])
            msg += f"\n  intervals:  {nms}"
            log.verbose(msg)
            # Construct the Scan frame
            frame = c3g.G3Frame(c3g.G3FrameType.Scan)
            # Add timestamps
            frame[self._timestamp_names[1]] = save_bookbinder_shared(
                obs,
                self._timestamp_names[0],
                view_name=frame_intervals,
                view_index=ivw,
                g3t=c3g.G3VectorTime,
            )
            for shr_key, shr_val, shr_type in self._shared_names:
                frame[shr_val] = save_bookbinder_shared(
                    obs,
                    shr_key,
                    view_name=frame_intervals,
                    view_index=ivw,
                    g3t=shr_type,
                )
            for det_key, det_val, det_type, det_opts in self._det_names:
                frame[det_val], gunits = save_bookbinder_detdata(
                    obs,
                    det_key,
                    view_name=frame_intervals,
                    view_index=ivw,
                    dtype=det_type,
                    times=self._timestamp_names[0],
                    options=det_opts,
                )
                # Record the original detdata type, so that it can be reconstructed
                # later.
                det_type_name = f"{det_val}_dtype"
                frame[det_type_name] = c3g.G3String(obs.detdata[det_key].dtype.char)

            # If we are exporting intervals, create an interval list with a single
            # interval for this frame.  Then use this repeatedly in the intersection
            # calculation.
            if len(self._interval_names) > 0:
                tview = obs.view[frame_intervals].shared[self._timestamp_names[0]][ivw]
                iframe = toast.intervals.IntervalList(
                    obs.shared[self._timestamp_names[0]],
                    timespans=[(tview[0], tview[-1])],
                )
                for ivl_key, ivl_val in self._interval_names:
                    frame[ivl_val] = save_bookbinder_intervals(
                        obs,
                        ivl_key,
                        iframe,
                    )
            output.append(frame)
        # Delete our temporary frame interval if we created it
        if self._frame_intervals is None:
            del obs.intervals[frame_intervals]

        return output
