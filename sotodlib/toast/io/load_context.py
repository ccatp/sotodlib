# Copyright (c) 2020-2021 Simons Observatory.
# Full license can be found in the top level "LICENSE" file.
"""Tools for loading data from a Context.

"""

import re

import toast


def select_obs_and_dets(
    context,
    obs_ids=None,
    obs_queries=None,
    obs_regex=None,
    detsets=None,
    dets=None,
    dets_regex=None,
    union=False,
):
    """Given a context, select observation IDs and detectors.

    This takes all the selection criteria and computes either the intersection or the
    union of the results.

    Args:
        obs_ids (list):  List of explicit observation IDs.
        obs_queries (list):  List of query strings for observations.
        obs_regex (str):  A regex string to apply to observation IDs for selection.
        detsets (list):  An explicit list of detector sets.
        dets (list):  An explicit list of detectors.
        dets_regex (str):  A regex string to apply to the detector names.
        union (bool):  If True, take the union of results, not the intersection.

    Returns:
        (tuple):  The list of observation IDs and detectors to use.

    """
    final_obs = list()
    final_dets = list()

    return final_obs, final_dets


def load_data(context, obs_ids, dets, comm=toast.Comm()):
    """Load specified observations and detectors into memory.

    The full observation and detector lists should be pre-selected (see for example
    the `select_obs_and_dets()` function).  This function uses the provided context
    to load each observation on one process and broadcast to the group of processes
    assigned to that observation.  Since the loading involves reading and concatenating
    frame files, it is best to do this once and communicate the result.

    NOTE:  currently the context exists on all processes (including loading the
    yaml file and opening databases).  This will likely not scale and we should
    refactor the Context class to handle this scenario.

    Args:
        context (Context):  The context to use.
        obs_ids (list):  The list of observation IDs to load.
        dets (list):  The list of detectors to load from each observation.
        comm (toast.Comm):  The toast communicator.

    Returns:
        (toast.Data):  The distributed toast Data object.

    """
    log = toast.utils.Logger.get()

    # the global communicator
    cworld = comm.comm_world
    # the communicator within the group
    cgroup = comm.comm_group

    # Normally, here is where we would (on one process) query the size of all
    # observations and distribute them among the process groups.  Unfortunately
    # Context.obsfiledb.get_files() does not reliably return a sample range for each
    # observation, so we cannot get the relative sizes of the observations.  For now,
    # just distribute them with equal weight.

    # One process gets the list of observation directories
    obslist = obs_ids
    # weight = dict()
    weight = {x: 1.0 for x in obslist}

    # worldrank = 0
    # if cworld is not None:
    #     worldrank = cworld.rank
    #
    # if worldrank == 0:
    #     # Get the weights...
    #
    # if cworld is not None:
    #     obslist = cworld.bcast(obslist, root=0)
    #     weight = cworld.bcast(weight, root=0)

    # Distribute observations based on approximate size
    dweight = [weight[x] for x in obslist]
    distobs = toast.dist.distribute_discrete(dweight, comm.ngroups)

    # Distributed data
    data = toast.Data(comm=comm)

    # Now every group loads its observations

    firstobs = distobs[comm.group][0]
    nobs = distobs[comm.group][1]
    for ob in range(firstobs, firstobs + nobs):
        telematch = re.match(r"CES-Atacama-(\w+)-.*", obslist[ob])
        if telematch is None:
            msg = "Cannot extract telescope name from {}".format(obslist[ob])
            raise RuntimeError(msg)
        telename = telematch.group(1)

        axmgr = None
        samples = None
        focal_plane = None
        if comm.group_rank == 0:
            # Load the data
            try:
                axmgr = context.get_obs(obs_id=obslist[ob], dets=dets)
                # Number of samples
                samples = axmgr.samps.count
                # Effective sample rate
                sample_rate = rate_from_times(axmgr.timestamps)
                # Create a Focalplane and Telescope and extract other metadata
                dets = axmgr.dets.vals
                quats = axmgr.focal_plane.quat
                det_quat = {d: q for d, q in zip(dets, quats)}
                focal_plane = Focalplane(
                    detector_data=det_quat, sample_rate=sample_rate
                )
                site = Site()
                tele = Telescope

            except:
                exc_type, exc_value, exc_traceback = sys.exc_info()
                lines = traceback.format_exception(exc_type, exc_value, exc_traceback)
                lines = ["Proc {}: {}".format(worldrank, x) for x in lines]
                print("".join(lines), flush=True)
                if cworld is not None:
                    cworld.Abort()

        # Broadcast meta

        # Create the observation.
        telescope = SOTelescope(telename)

        obs = Observation(telescope, name=obslist[ob], samples=samples)

        # Create data members.

        # Add to the data object
        data.obs.append(obs)

    return data



