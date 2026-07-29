"""
cite process to convert sources and metasources into full citations
"""

import traceback
from importlib import import_module
from pathlib import Path
from dotenv import load_dotenv
from util import *


# load environment variables
load_dotenv()


# save errors/warnings for reporting at end
errors = []
warnings = []

# output citations file
output_file = "_data/citations.yaml"


log()

log("Compiling sources")

# compiled list of sources
sources = []

# in-order list of plugins to run
plugins = ["google-scholar", "pubmed", "orcid", "sources"]

# loop through plugins
for plugin in plugins:
    # convert into path object
    plugin = Path(f"plugins/{plugin}.py")

    log(f"Running {plugin.stem} plugin")

    # get all data files to process with current plugin
    files = Path.cwd().glob(f"_data/{plugin.stem}*.*")
    files = list(filter(lambda p: p.suffix in [".yaml", ".yml", ".json"], files))

    log(f"Found {len(files)} {plugin.stem}* data file(s)", indent=1)

    # loop through data files
    for file in files:
        log(f"Processing data file {file.name}", indent=1)

        # load data from file
        try:
            data = load_data(file)
            # check if file in correct format
            if not list_of_dicts(data):
                raise Exception(f"{file.name} data file not a list of dicts")
        except Exception as e:
            log(e, indent=2, level="ERROR")
            errors.append(e)
            continue

        # loop through data entries
        for index, entry in enumerate(data):
            log(f"Processing entry {index + 1} of {len(data)}, {label(entry)}", level=2)

            # run plugin on data entry to expand into multiple sources
            try:
                expanded = import_module(f"plugins.{plugin.stem}").main(entry)
                # check that plugin returned correct format
                if not list_of_dicts(expanded):
                    raise Exception(f"{plugin.stem} plugin didn't return list of dicts")
            # catch any plugin error
            except Exception as e:
                # log detailed pre-formatted/colored trace
                print(traceback.format_exc())
                # log high-level error
                log(e, indent=3, level="ERROR")
                errors.append(e)
                continue

            # loop through sources
            for source in expanded:
                if plugin.stem != "sources":
                    log(label(source), level=3)

                # include meta info about source
                source["plugin"] = plugin.name
                source["file"] = file.name

                # add source to compiled list
                sources.append(source)

            if plugin.stem != "sources":
                log(f"{len(expanded)} source(s)", indent=3)


log("Merging sources by id")

# merge sources with matching (non-blank) ids
for a in range(0, len(sources)):
    a_id = get_safe(sources, f"{a}.id", "")
    if not a_id:
        continue
    for b in range(a + 1, len(sources)):
        b_id = get_safe(sources, f"{b}.id", "")
        if b_id == a_id:
            log(f"Found duplicate {b_id}", indent=2)
            sources[a].update(sources[b])
            sources[b] = {}
sources = [entry for entry in sources if entry]


log(f"{len(sources)} total source(s) to cite")


log()

log("Generating citations")

# list of new citations
citations = []


# loop through compiled sources
for index, source in enumerate(sources):
    log(f"Processing source {index + 1} of {len(sources)}, {label(source)}")

    # if explicitly flagged, remove/ignore entry
    if get_safe(source, "remove", False) == True:
        continue

    # new citation data for source
    citation = {}

    # source id
    _id = get_safe(source, "id", "").strip()

    # manubot doesn't work without an id
    if _id:
        log("Using Manubot to generate citation", indent=1)

        try:
            # run manubot and set citation
            citation = cite_with_manubot(_id)

        # if manubot cannot cite source
        except Exception as e:
            plugin = get_safe(source, "plugin", "")
            file = get_safe(source, "file", "")
            # if regular source (id entered by user), throw error
            if plugin == "sources.py":
                log(e, indent=3, level="ERROR")
                errors.append(f"Manubot could not generate citation for source {_id}")
            # otherwise, if from metasource (id retrieved from some third-party api), just warn
            else:
                log(e, indent=3, level="WARNING")
                warnings.append(
                    f"Manubot could not generate citation for source {_id} (from {file} with {plugin})"
                )
                # discard source from citations
                continue

    # preserve fields from input source, overriding existing fields
    citation.update(source)

    # ensure date in proper format for correct date sorting
    if get_safe(citation, "date", ""):
        citation["date"] = format_date(get_safe(citation, "date", ""))

    # add new citation to list
    citations.append(citation)


log()
log("Removing duplicate versions of the same work")

import re

# doi prefixes that indicate a preprint server
preprint_prefixes = (
    "10.26434",  # chemrxiv
    "10.21203",  # research square
    "10.1101",  # biorxiv / medrxiv
    "10.31219",  # osf preprints
    "10.20944",  # preprints.org
    "10.22541",  # authorea
)


def strip_version(_id):
    """strip version suffix, e.g. chemrxiv-2025-7s3gx/v2 -> chemrxiv-2025-7s3gx"""
    return re.sub(r"[/.-]v\d+$", "", str(_id).strip().lower())


def normalize_title(title):
    """lowercase title with punctuation and extra spaces removed, for comparing"""
    return re.sub(r"[^a-z0-9]+", " ", str(title).lower()).strip()


def is_preprint(citation):
    """guess whether citation is a preprint rather than a published version"""
    _id = str(get_safe(citation, "id", "")).lower()
    doi = _id.replace("doi:", "")
    publisher = str(get_safe(citation, "publisher", "")).lower()
    if _id.startswith("arxiv:") or doi.startswith(preprint_prefixes):
        return True
    servers = ["chemrxiv", "biorxiv", "medrxiv", "arxiv", "research square", "preprint"]
    return any(server in publisher for server in servers)


def version_sort(citation):
    """sort key, lower first, i.e. the version worth keeping"""
    date = str(get_safe(citation, "date", "")).replace("-", "")
    return (
        # published version beats preprint
        1 if is_preprint(citation) else 0,
        # hand-curated entry beats one auto-fetched from orcid/scholar/pubmed
        0 if get_safe(citation, "plugin", "") == "sources.py" else 1,
        # newest beats oldest
        -int(date or 0),
    )


# pick one citation per work, keeping the best version of each
keep = []
for citation in sorted(citations, key=version_sort):
    _id = strip_version(get_safe(citation, "id", ""))
    title = normalize_title(get_safe(citation, "title", ""))
    match = next(
        (
            other
            for other in keep
            if (_id and strip_version(get_safe(other, "id", "")) == _id)
            or (title and normalize_title(get_safe(other, "title", "")) == title)
        ),
        None,
    )
    if match:
        log(f"Skipping {label(citation)}, duplicate of {label(match)}", indent=1)
        continue
    keep.append(citation)

# keep original (date-sorted) order
keep_ids = [id(citation) for citation in keep]
citations = [citation for citation in citations if id(citation) in keep_ids]

log(f"{len(citations)} citation(s) after removing duplicate versions")

log()


log("Saving updated citations")


# save new citations
try:
    save_data(output_file, citations)
except Exception as e:
    log(e, level="ERROR")
    errors.append(e)


log()


# exit at end, so user can see all errors/warnings in one run
if len(warnings):
    log(f"{len(warnings)} warning(s) occurred above", level="WARNING")
    for warning in warnings:
        log(warning, indent=1, level="WARNING")

if len(errors):
    log(f"{len(errors)} error(s) occurred above", level="ERROR")
    for error in errors:
        log(error, indent=1, level="ERROR")
    log()
    exit(1)

else:
    log("All done!", level="SUCCESS")

log()
