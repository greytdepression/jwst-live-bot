import sys
import json
import functools
import subprocess
import datetime
import os
import urllib.request
from pypdf import PdfReader
import csv

categories_line = 3
first_obs_line = 5

stellarium_exe = "C:\\Program Files\\Stellarium\\stellarium.exe"

def get_line(lines, line_num):
    return lines[line_num - 1]

def get_categories(lines):
    line = get_line(lines, categories_line)

    cats = []

    state = 0
    word_start = 0
    word_end = 0
    for i in range(len(line)):
        match state:
            case 0:
                if line[i] == ' ':
                    state = 1
            case 1:
                if line[i].isalpha():
                    state = 0
                elif line[i] == ' ':
                    state = 2
                    word_end = i - 1
            case 2:
                if line[i].isalpha():
                    cats.append((line[word_start:word_end], word_start, i))
                    word_start = i
                    state = 0

    cats.append((line[word_start:word_end], word_start, 9999))

    return cats

def parse_line(lines, line_num, cats):
    line = get_line(lines, line_num)
    line_len = len(line)

    data = dict()

    for (cat_name, cat_start, cat_end) in cats:
        match cat_name:
            case 'VISIT TYPE' | 'SCIENCE INSTRUMENT AND MODE':
                data[cat_name] = set([line[cat_start:cat_end].strip()])
            case _:
                data[cat_name] = line[cat_start:cat_end].strip()

    return data

stellarium_script_prelude = """
// pause time playback
core.setTimeRate(0)

// disable GUI
core.setGuiVisible(false)

// enable only azimuthal grid
GridLinesMgr.setFlagAzimuthalGrid(true)
GridLinesMgr.setFlagEquatorGrid(false)

// turn on constellation lines and lables
ConstellationMgr.setFlagLines(true)
ConstellationMgr.setFlagLabels(true)

// BEGIN LOOP
"""

stellarium_script_postlude = """
// END LOOP

// quit application
core.quitStellarium()
"""

def add_stellarium_obs(obs):
    date = obs["SCHEDULED START TIME"][:-1]
    ra = f'"{obs["ra"]}"'
    dec = f'"{obs["dec"]}"'
    return f"""
// set date
core.setDate("{date}", "utc", true)

// move to correct location
core.moveToRaDecJ2000({ra}, {dec}, 0)

// add marker
MarkerMgr.markerEquatorial({ra}, {dec}, true, true, "cross", "#ff3366", 15.0, false, 0, true)

// take screenshot
core.screenshot("screenshot_{obs["VISIT ID"].replace(":", "_")}", false, "", true, "")

MarkerMgr.deleteAllMarkers()

core.wait(0.1)

"""

def parse_observations(input_file):
    lines = []
    with open(input_file, 'r') as file:
        lines = file.readlines()

    cats = get_categories(lines)
    observations = []

    for i in range(first_obs_line, len(lines)):
        observations.append(parse_line(lines, i, cats))

    # deduplicate attached observations
    i = 0
    while i < len(observations):
        if observations[i]['SCHEDULED START TIME'] == '^ATTACHED TO PRIME^':
            observations[i-1]['VISIT TYPE'].update(observations[i]['VISIT TYPE'])
            observations[i-1]['SCIENCE INSTRUMENT AND MODE'].update(observations[i]['SCIENCE INSTRUMENT AND MODE'])
            observations.pop(i)
        elif observations[i]["VISIT ID"] == "":

            # it would be nice to list all the targets, but for now we'll just list the primary one

            observations.pop(i)
        elif observations[i]["CATEGORY"] == "Calibration":
            observations.pop(i)
        else:
            i += 1

    # turn sets into lists again
    for obs in observations:
        # 'VISIT TYPE' | 'SCIENCE INSTRUMENT AND MODE'
        cats = ['VISIT TYPE', 'SCIENCE INSTRUMENT AND MODE']

        for cat in cats:
            obs[cat] = list(obs[cat])

    return observations

def obs_visit_id_key(obs):
    nums = obs["VISIT ID"].split(':')
    return int(nums[0]) * 1_000_000_000_000 + int(nums[1]) * 1_000_000 + int(nums[2])

def prepare_csv(observations, out_file):
    # Sort by VISIT ID
    sorted_obs = sorted(observations, key = obs_visit_id_key)

    with open(out_file, 'w') as file:
        writer = csv.writer(file)

        writer.writerow([
            "Proposal",
            "Observation",
            "Num",
            "Link",
            "RA",
            "Dec",
            "PI",
            "PI Institution",
            "Title",
            "Abstract"
        ])

        for obs in sorted_obs:
            if "ra" in obs:
                continue

            proposal_id = int(obs['VISIT ID'].split(':')[0])
            obs_id = int(obs['VISIT ID'].split(':')[1])
            obs_2nd_num = int(obs['VISIT ID'].split(':')[2])
            proposal_link = f"https://www.stsci.edu/jwst/phase2-public/{proposal_id}.pdf"

            val_or_empty = lambda k: obs[k] if k in obs else ""

            ra = val_or_empty("ra")
            dec = val_or_empty("dec")
            pi = val_or_empty("pi name")
            pi_inst = val_or_empty("pi institution")
            title = val_or_empty("title")
            abstract = val_or_empty("abstract")

            writer.writerow([
                proposal_id,
                obs_id,
                obs_2nd_num,
                proposal_link,
                ra,
                dec,
                pi,
                pi_inst,
                title,
                abstract,
            ])

def try_autofill_data(observations):
    visit_ids = set()
    for obs in observations:
        visit_ids.add(int(obs["VISIT ID"].split(":")[0]))
    print("Downloading all proposal PDFs...")

    if not os.path.exists("cache/"):
        os.makedirs("cache/")

    no_proposal = []
    for vid in visit_ids:
        if not os.path.isfile(f"cache/{vid}.pdf"):
            success = True
            print(f"Downloading proposal #{vid}...", end='')
            try:
                urllib.request.urlretrieve(f"https://www.stsci.edu/jwst/phase2-public/{vid}.pdf", f"cache/{vid}.pdf")
            except Exception:
                success = False
                print(f" Failed!")
                no_proposal.append(vid)

            if success:
                print(" Done!")

    if len(no_proposal) > 0:
        print(f"Failed to download {len(no_proposal)} proposal(s). Please fill out details manually in the generated CSV file!")

    for vid in no_proposal:
        visit_ids.remove(vid)

    print("Done retrieving PDFs")

    for vid in visit_ids:
        try_parse_proposal_data(vid, observations)

    return observations

def try_parse_proposal_data(vid, observations):
    proposal_title = ""
    proposal_investigators = []
    proposal_abstract = ""
    proposal_observations = dict()
    proposal_targets = dict()

    with PdfReader(f"cache/{vid}.pdf") as reader:
        proposal_title = proposal_get_title(reader.pages, vid)
        proposal_investigators = proposal_get_co_investigators(reader.pages, vid)
        proposal_abstract = proposal_get_abstract(reader.pages, vid)
        proposal_observations = proposal_get_observations(reader.pages, vid)
        proposal_targets = proposal_get_targets(reader.pages, vid)

    for obs in observations:
        if int(obs["VISIT ID"].split(":")[0]) != vid:
            continue

        obs["title"] = proposal_title
        obs["pi name"] = proposal_investigators[0][0]
        obs["pi institution"] = proposal_investigators[0][1]
        obs["abstract"] = proposal_abstract
        obs["co-investigators"] = proposal_investigators[1:]

        obs_id = int(obs["VISIT ID"].split(":")[1])

        if obs_id not in proposal_observations:
            print(f"Observation {obs_id} not in proposal {vid}.")
            continue

        (target_num, target_name) = proposal_observations[obs_id]

        if target_num is not None:
            (target_name_, target_coords) = proposal_targets[target_num]
            assert(target_name == target_name_)

            if target_coords is not None:
                obs["ra"] = target_coords[0]
                obs["dec"] = target_coords[1]
            else:
                print(f"Proposal {vid}, Observation {obs_id}, Science target {target_num}: No RA and Dec available.")



def proposal_header(page):
    return page.extract_text(extraction_mode="layout").splitlines()[0]

def proposal_is_page_overview(page, proposal_id):
    return proposal_header(page).startswith(f"JWST Proposal {proposal_id}") and proposal_header(page).endswith("- Overview")

def proposal_is_page_targets(page, proposal_id):
    return proposal_header(page).startswith(f"Proposal {proposal_id} - Targets")

def proposal_get_lines(page):
    return page.extract_text(extraction_mode="layout").splitlines()

def proposal_debug_print_line(proposal_id, line, sections):
    print(f"========= {proposal_id} =========")
    print(line)
    for sec in sections:
        print(" " * sec[1] + f"^ {sec[0]}")
    print(f"========= {proposal_id} =========")

def proposal_get_observations(pages, proposal_id):
    observations = dict()
    observations_block = False
    for i in range(len(pages)):
        if not proposal_is_page_overview(pages[i], proposal_id):
            return None
        lines = proposal_get_lines(pages[i])
        obs_col = 0
        label_col = 0
        target_col = 0
        for l in range(1, len(lines) - 1):
            if lines[l] == "OBSERVATIONS":
                observations_block = True
                continue
            if not observations_block:
                continue
            if lines[l].startswith("Folder"):
                obs_col = lines[l].find("Observation")
                label_col = lines[l].find("Label")
                observing_template_col = lines[l].find("Observing Template")
                target_col = lines[l].find("Science Target")
                #proposal_debug_print_line(proposal_id, lines[l], [("Observation", obs_col), ("Label", label_col), ("Science Target", target_col)])
                continue
            if lines[l] == "ABSTRACT":
                return observations
            if lines[l] == "":
                continue
            if lines[l][:obs_col].strip() != "":
                continue
            if lines[l][obs_col:label_col].strip() == "":
                continue
            #proposal_debug_print_line(proposal_id, lines[l], [("Observation", obs_col), ("Label", label_col), ("Science Target", target_col)])
            obs_num = int(lines[l][obs_col:label_col].strip())
            # 1. sometimes pypdf messes up and doesn't insert the spaces before the science target, messing up alignment
            # 2. sometimes there is no target :/
            science_target_num = None
            science_target_name = None
            if "(" in lines[l][observing_template_col:]:
                science_target_num = int(lines[l][observing_template_col:].split("(")[1].split(")")[0])
                science_target_name = lines[l][observing_template_col:].split(")")[1].strip()
            observations[obs_num] = (science_target_num, science_target_name)

def proposal_get_title(pages, proposal_id):
    for i in range(len(pages)):
        if not proposal_is_page_overview(pages[i], proposal_id):
            return None
        lines = proposal_get_lines(pages[i])
        # skip the first line as it only contains the header
        start_line = 0
        end_line = 0
        for l in range(1, len(lines)):
            if lines[l] == "":
                continue
            start_line = l
            for j in range(l+1, len(lines)):
                if lines[j].startswith("Cycle: "):
                    end_line = j
                    break
            break
        return " ".join(lines[start_line:end_line])

def proposal_get_text(pages, proposal_id, start_page, start_line, end_page, end_line):
    lines = []
    for page_i in range(start_page, end_page + 1):
        # skip the header line and the page number line
        s = 1 if page_i != start_page else start_line
        e = -1 if page_i != end_page else end_line
        pl = proposal_get_lines(pages[page_i])
        for j in range(s, e):
            if pl[j] != "":
                lines.append(pl[j])
    return " ".join(lines)

def proposal_get_abstract(pages, proposal_id):
    abstract_lines = []
    abstract_block = False
    for i in range(len(pages)):
        if not proposal_is_page_overview(pages[i], proposal_id):
            return None
        lines = proposal_get_lines(pages[i])
        for l in range(1, len(lines) - 1):
            if lines[l] == "ABSTRACT":
                abstract_block = True
                continue
            if not abstract_block:
                continue
            if lines[l] == "OBSERVING DESCRIPTION":
                return " ".join(abstract_lines)
            if lines[l] == "":
                continue
            abstract_lines.append(lines[l])

def proposal_get_co_investigators(pages, proposal_id):
    co_investigators = []
    investigator_block = False
    for i in range(len(pages)):
        if not proposal_is_page_overview(pages[i], proposal_id):
            return None
        lines = proposal_get_lines(pages[i])
        inst_col = 0
        for l in range(1, len(lines) - 1):
            if lines[l] == "INVESTIGATORS":
                investigator_block = True
                continue
            if not investigator_block:
                continue
            if lines[l].startswith("Name"):
                inst_col = lines[l].find("Institution")
                continue
            if lines[l] == "OBSERVATIONS":
                return co_investigators
            if lines[l] == "":
                continue
            name = lines[l][:inst_col].split("(")[0].strip()
            inst = " ".join(lines[l][inst_col:].replace(",", " - ").split())
            co_investigators.append((name, inst))

def proposal_get_targets(pages, proposal_id):
    targets = dict()
    for i in range(len(pages)):
        if proposal_is_page_overview(pages[i], proposal_id):
            continue
        if not proposal_is_page_targets(pages[i], proposal_id):
            return targets
        lines = proposal_get_lines(pages[i])
        name_col = 0
        target_coords_col = 0
        target_coords_corrections_col = 0
        for l in range(1, len(lines) - 1):
            if len(lines) > 0 and lines[l].lstrip().startswith("("):
                if name_col == 0:
                    items = list(filter(lambda s: len(s) > 0, map(lambda s: s.strip(), lines[l].split("   "))))
                    name_col = lines[l].find(items[1])
                    target_coords_col = lines[l].find(items[2])
                target_num = int(lines[l][:name_col].split("(")[1].split(")")[0])
                target_name = lines[l][name_col:target_coords_col].strip()
                target_coords = None
                if lines[l][target_coords_col:].startswith("RA:"):
                    ra = lines[l][target_coords_col:].split("(")[1].split(")")[0]
                    dec = lines[l+1][target_coords_col:].split("(")[1].split(")")[0]
                    target_coords = (ra, dec)
                targets[target_num] = (target_name, target_coords)

def insert_position_data(observations, csv_file):
    csv_data = []
    with open(csv_file) as file:
        for line in file.readlines()[1:]:

            if '"' in line:
                print("ERROR: CSV contains quotation marks. This script can't parse those.")
                exit(1)

            values = line.split(",")
            visit_id = f"{values[0]}:{values[1]}:{values[2]}"
            ra = values[4]
            dec = values[5]

            csv_data.append((visit_id, ra, dec))

    for (visit_id, ra, dec) in csv_data:
        for obs in observations:
            if obs["VISIT ID"] == visit_id:
                obs["ra"] = ra
                obs["dec"] = dec

def insert_manual_csv_data(observations, csv_file):
    with open(csv_file) as file:
        reader = csv.reader(file)
        for line in reader:

            if line[0] == "Proposal":
                continue

            #            0        1           2   3    4  5   6  7              8     9
            # csv_out = "Proposal,Observation,Num,Link,RA,Dec,PI,PI Institution,Title,Abstract\n"

            val = lambda i: line[i] if i < len(line) and len(line[i]) > 0 else None

            visit_id = f"{val(0)}:{val(1)}:{val(2)}"
            ra = val(4)
            dec = val(5)
            pi = val(6)
            pi_inst = val(7)
            title = val(8)
            abstract = val(9)

            for obs in observations:
                if obs["VISIT ID"] == visit_id:
                    obs["title"] = title
                    obs["abstract"] = abstract
                    obs["pi name"] = pi
                    obs["pi institution"] = pi_inst
                    if "co-investigators" not in obs:
                        obs["co-investigators"] = []
                    obs["ra"] = ra
                    obs["dec"] = dec

def make_stellarium_script(observations):
    stellarium_script = stellarium_script_prelude

    for obs in observations:
        if obs["CATEGORY"] == "Calibration":
            continue

        if "ra" not in obs:
            continue

        stellarium_script += add_stellarium_obs(obs)

    stellarium_script += stellarium_script_postlude
    return stellarium_script

def make_metadata_dict(observations):
    output_array = []
    for obs in observations:
        if obs["CATEGORY"] == "Calibration":
            continue

        output_dict = dict()

        val_or_na = lambda k: obs[k] if (k in obs and obs[k] is not None) else "N/A"

        visit_id = obs["VISIT ID"]
        output_dict["visit_id"] = visit_id
        output_dict["proposal_id"] = visit_id.split(":")[0]
        output_dict["observation"] = visit_id.split(":")[1]
        output_dict["start_date"] = obs["SCHEDULED START TIME"].split("T")[0]
        output_dict["start_time"] = obs["SCHEDULED START TIME"].split("T")[1][:-1]
        output_dict["target_name"] = obs["TARGET NAME"]
        output_dict["duration"] = obs["DURATION"]
        output_dict["pi"] = val_or_na("pi name")
        output_dict["pi_inst"] = val_or_na("pi institution")
        output_dict["title"] = val_or_na("title")
        output_dict["image"] = f"screenshot_{obs['VISIT ID'].replace(':', '_')}.png" if ("ra" in obs and obs["ra"] is not None) else "N/A"
        output_dict["category"] = obs["CATEGORY"]
        output_dict["keywords"] = obs["KEYWORDS"]
        output_dict["abstract"] = val_or_na("abstract")
        output_dict["co-investigators"] = val_or_na("co-investigators")

        inst_plus_modes = [inst for inst in obs["SCIENCE INSTRUMENT AND MODE"] if len(inst) > 0]
        output_dict["inst_plus_mode"] = inst_plus_modes
        output_dict["instruments"] = []

        for inst_mode in inst_plus_modes:
            match inst_mode.split(" ")[0]:
                case "NIRSpec" | "MIRI" | "NIRCam" | "NIRISS":
                    output_dict["instruments"].append(inst_mode.split(" ")[0])
                case _:
                    if inst_mode.startswith("WFSC NIRCam"):
                        output_dict["instruments"].append("NIRCam")
                    else:
                        print(f"{obs['VISIT ID']}: unrecognized instrument `{inst_mode}`")
                        print("add case for this instrument, then rerun script")
                        exit(1)
        output_array.append(output_dict)
    return output_array

def get_instrument_vis(inst):
    match inst:
        case "NIRSpec":
            return "https://staging.cohostcdn.org/attachment/c6533fd7-158d-40cc-b5e5-f2323841b271/NIRSpec_vis.png"
        case "MIRI":
            return "https://staging.cohostcdn.org/attachment/4690ddbb-8ea3-4470-88f6-7aec5afd027a/MIRI_vis.png"
        case _:
            return None

def get_instrument_wikipedia(inst):
    match inst:
        case "NIRSpec":
            return "[NIRSpec](https://en.wikipedia.org/wiki/NIRSpec) - Near-InfraRed Spectograph"
        case "MIRI":
            return "[MIRI](https://en.wikipedia.org/wiki/Mid-Infrared_Instrument) - Mid-InfraRed Instrument"
        case "NIRCam":
            return "[NIRCam](https://en.wikipedia.org/wiki/NIRCam) - Near-InfraRed Camera"
        case "NIRISS":
            return "[FGS-NIRISS](https://en.wikipedia.org/wiki/Fine_Guidance_Sensor_and_Near_Infrared_Imager_and_Slitless_Spectrograph) - Fine Guidance Sensor and Near-InfraRed Imager and Slitless Spectrograph"

def make_chosts(metadata_array):
    chosts = []

    for metadata in metadata_array:

        if metadata["title"] == "N/A":
            continue

        chost = {
            "post_time": f"{metadata['start_date']} {metadata['start_time']} UTC",
        }

        chost["title"] = f"{metadata['title']}"
        body = []
        body.append({
            "type": "markdown",
            "value": f"<b>Principal Investigator:</b> {metadata['pi']} ({metadata['pi_inst']})",
        })

        if metadata["image"] != "N/A":
            body.append({
                "type": "image",
                "value": metadata["image"],
                # TODO: maybe we could add a little more context as to where in the sky it is relative to constellations and such.
                "alt_text": f"A map of the sky indicating where {metadata['target_name']} is located.",
            })
            body.append({
                "type": "markdown",
                "value": f"<p style='text-align:center;'><b>Target:</b> {metadata['target_name']}</p>",
            })
        else:
            body.append({
                "type": "markdown",
                "value": f"<b>Target:</b> {metadata['target_name']}",
            })

        obs_time = metadata["start_time"]
        obs_time_h = obs_time.split(":")[0]
        obs_time_ms = ':'.join(obs_time.split(":")[1:])
        obs_time_am_pm = "AM" if int(obs_time_h) < 12 else "PM"
        obs_time_h_12h = "12" if (int(obs_time_h) % 12) == 0 else f"{(int(obs_time_h) % 12):02d}"

        obs_duration = metadata["duration"]
        obs_duration_d = int(obs_duration.split("/")[0])
        obs_duration_h = int(obs_duration.split("/")[1].split(":")[0])
        obs_duration_m = int(obs_duration.split("/")[1].split(":")[1])
        obs_duration_s = int(obs_duration.split("/")[1].split(":")[2])

        obs_duration_out_str = "" if obs_duration_d == 0 else ("1 day " if obs_duration_d == 1 else f"{obs_duration_d} days ")
        obs_duration_out_str += "" if (obs_duration_d == 0 and obs_duration_h == 0) else ("1 hour " if obs_duration_h == 1 else f"{obs_duration_h} hours ")
        obs_duration_out_str += "" if (obs_duration_d == 0 and obs_duration_h == 0 and obs_duration_m == 0) else f"{obs_duration_m} min "
        obs_duration_out_str += f"{obs_duration_s} sec"

        body.append({
            "type": "markdown",
            "value": f"""<b>Scheduled Observation Start:</b> {metadata['start_date']} at {metadata['start_time']} UTC ({obs_time_h_12h}:{obs_time_ms} {obs_time_am_pm})
<b>Duration:</b> {obs_duration_out_str}""",
        })

        body.append({
            "type": "markdown",
            "value": "---",
        })

        if metadata["abstract"] != "N/A":
            body.append({
                "type": "markdown",
                "value": f"<b>Abstract:</b> <p>{metadata['abstract']}</p>",
            })

        if metadata["co-investigators"] != "N/A" and len(metadata["co-investigators"]) > 0:
            value = """<b>Co-Investigators:</b>
<table>
    <tr>
        <th style="text-align:left;">Name</th>
        <th style="text-align:left;">Institution</th>
    </tr>
"""
            for inv in metadata["co-investigators"]:
                value += f"""   <tr>
        <td>{inv[0]}</td>
        <td>{inv[1]}</td>
    </tr>
"""
            value += "</table>"
            body.append({
                "type": "markdown",
                "value": value,
            })

        instruments_body = ""

        instruments_deduped = []
        for inst in metadata["instruments"]:
            if inst not in instruments_deduped:
                instruments_deduped.append(inst)

        if len(instruments_deduped) == 1:
            instruments_body += f"<b>Instrument:</b> "
        elif len(instruments_deduped) > 1:
            instruments_body += f"<b>Instruments:</b> "


        for inst in instruments_deduped:
            instruments_body += f"{get_instrument_wikipedia(inst)}\n"
            visual = get_instrument_vis(inst)
            if visual is not None:
                instruments_body += f"![A computer rendering of the {inst} module]({visual})\n"

        body.append({
            "type": "markdown",
            "value": instruments_body,
        })

        chost["body"] = body

        # TAGS
        tags = [
            "jwst",
            "jwst live bot",
            "astronomy",
            "cosmology",
            "space telescope",
            "james webb space telescope",
            "webb space telescope",
            "NASA",
            "bot account",
            "automated post",
            "cohost.py",
            "The Cohost Bot Feed",
            f"Category: {metadata['category']}",
        ]

        for kw in metadata["keywords"].split(","):
            tags.append(kw.strip())

        chost["tags"] = tags

        chosts.append(chost)
    return chosts


def show_help():
    print(f"""Usage: python {sys.argv[0]} [command] [args...]

        Commands:
            preprocess <input txt file>                                 - Processes the data and outputs CSV file to fill in observation coordinates.
            compile <input txt file> [--exclude <PROPOSAL>*] - Compiles the txt file and the CSV file into the final output. Excludes all listed proposal IDs
            help                                                        - Displays this help page
        """)

if __name__ == '__main__':
    if len(sys.argv) == 1:
        show_help()
        exit(1)

    start_time = datetime.datetime.now()

    command = sys.argv[1]

    match command:
        case "preprocess":
            if len(sys.argv) != 3:
                show_help()
                exit(1)

            input_file = sys.argv[2]
            output_csv_file = input_file + '.manual.csv'
            output_json_file = input_file + '.auto.json'

            observations = parse_observations(input_file)

            observations = try_autofill_data(observations)
            prepare_csv(observations, output_csv_file)

            with open(output_json_file, 'w') as file:
                json.dump(observations, file, ensure_ascii = True, indent = 2)

            print("\nDone precompiling data")
            print(f"Automatically detected data: {output_json_file}")
            print(f"Please manually fill in missing data in {output_csv_file}. When done, run `compile`.\n")
        case "compile":
            if len(sys.argv) < 3:
                show_help()
                exit(1)

            input_file = sys.argv[2]

            # Check for exclusions
            exclusions = []
            if len(sys.argv) > 3:
                if not sys.argv[3] == "--exclude":
                    show_help()
                    exit(1)

                for pid in sys.argv[4:]:
                    exclusions.append(int(pid))

            manual_csv_file = input_file + '.manual.csv'
            observations_json_file = input_file + '.auto.json'

            # load observations from json file
            observations = []
            with open(observations_json_file, 'r') as file:
                observations = json.load(file)

            # load manually entered data
            insert_manual_csv_data(observations, manual_csv_file)

            # throw out any observation that does not have a title
            # or have been manually excluded
            observations = [obs for obs in observations if ("title" in obs and obs["title"] is not None and int(obs["VISIT ID"].split(":")[0]) not in exclusions)]

            # create dir
            dir_name = datetime.datetime.now().strftime("%Y_%m_%d")
            os.makedirs(f"./output/{dir_name}/screenshots/")


            output_ssc_file = os.path.abspath(f"./output/{dir_name}/screenshot_script.ssc")
            script = make_stellarium_script(observations)
            with open(output_ssc_file, 'w') as file:
                file.write(script)

            output_metadata_file = f"./output/{dir_name}/metadata.json"
            metadata = make_metadata_dict(observations)
            with open(output_metadata_file, 'w') as file:
                json.dump(metadata, file, ensure_ascii = True, indent = 2)

            output_chosts_file = f"./output/{dir_name}/chosts.json"
            chosts = make_chosts(metadata)
            with open(output_chosts_file, 'w') as file:
                json.dump(chosts, file, ensure_ascii = True, indent = 2)

            subprocess.run([
                stellarium_exe,
                "--screenshot-dir", os.path.abspath(f"./output/{dir_name}/screenshots/"),
                "--full-screen", "yes",
                "--fov", "40",
                "--projection-type", "ProjectionFisheye",
                "--startup-script", output_ssc_file,
            ])

        case _:
            show_help()

    end_time = datetime.datetime.now()
    duration = (end_time - start_time).total_seconds()
    print(f"\nFinished after {duration} seconds\n")
