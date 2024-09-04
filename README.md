# JWST live bot

All the scripts that make up the [JWST live bot](https://cohost.org/JWST-live). Based on [Cohost.py](https://github.com/valknight/Cohost.py).

The chost data is prepared weekly using the `jwst-observation-parser.py` script and then uploaded to a Raspberry Pi Zero W running the `automation_script_v0.py` script which then posts the chosts throughout the week.

> [!IMPORTANT]  
> The purpose of these scripts was for me to make the [JWST live bot](https://cohost.org/JWST-live) and as such my mantra was "it works on my machine".
> I cannot guarantee that it will work on yours.

## The Data Parsing Stage
### Overview
The [Space Telescope Science Institute](https://www.stsci.edu) publishes [weekly observation schedules](https://www.stsci.edu/jwst/science-execution/observing-schedules) for the JWST
and also hosts the research proposal PDFs describing what the observation data will be used for (see e.g. proposal [#4567](https://www.stsci.edu/jwst/phase2-public/4567.pdf) or,
for a very long proposal, [#5645](https://www.stsci.edu/jwst/phase2-public/5645.pdf)).

The weekly observation schedule (see e.g. [Sep 1 to Sep 8, 2024](https://www.stsci.edu/files/live/sites/www/files/home/jwst/science-execution/observing-schedules/_documents/20240901_report_20240829.txt))
is a txt file with a table structure. The rows contain the individual observations, which are identified by their `VISIT ID` which has the form `PROPOSAL:OBSERVATION:NUM`.
The `PROPOSAL` part identifies which proposal the observation is for, while the `OBSERVATION` part specifies which of the proposal's potentially multiple observations it is
(see the **OBSERVATIONS** table in the proposal PDFs).

The proposal PDFs contain information about the title of the proposal and the investigators behind it. It also gives the abstract for what the proposal aims to investigate.
Furthermore, these PDFs contain the data about the various observations (specifically what the "science target" is) and information about where to find said science targets.

### Usage of `jwst-observation-parser.py` script
Make sure you are using Python 3 and ideally create a virutal environment to run the script in. Install PyPDF2 (`pip install PyPDF2`) and any other packages it complains about missing :p
The script assumes you have [Stellarium](https://stellarium.org/) installed and it's located at `C:\Program Files\Stellarium\stellarium.exe`. If it is somewhere else on your system or
you don't use Windows, then update the `stellarium_exe` global variable to your path to the Stellarium executable.

> [!WARNING]  
> The script is not very stable. I expect to run into some new issues every week for the next couple of weeks. If the script fails running at some point, you'll either have to wait
> for me to push an update or you'll have to fix it yourself.

Now follow these steps:
1. Download the latest weekly observation schedule from [STScI](https://www.stsci.edu/jwst/science-execution/observing-schedules).
2. Run `python jwst-observation-parser.py preprocess path/to/schedule.txt`
3. The script will now try and parse the observation schedule, download all relevant proposal PDFs, and parse the data from those.
4. If it fails to download one ore more of the proposals, the program will inform you about it and ask you to manually fill in the data for those in the generated CSV file.
5. Try to fill out the CSV file with any additional data you can find. Delete the rows for which you could not find anything.
6. Run `python jwst-observation-parser.py compile path/to/schedule.txt`. You should notice that Stellarium starts shortly after executing the command. Wait for it to close automatically.
7. You're done! You should now find all the generated files in `output/[today's date]`.


