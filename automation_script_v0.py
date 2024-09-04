from datetime import datetime
import pytz
import time
import os
from cohost.models.user import User
from cohost.models.block import AttachmentBlock, MarkdownBlock
import sys
import json
import sched

username = ""
password = ""
handle = ""

def post_chost(chost, base_dir):
    blocks = []

    for block in chost["body"]:
        match block["type"]:
            case "markdown":
                blocks.append(MarkdownBlock(block["value"]))
            case "image":
                blocks.append(AttachmentBlock(os.path.join(base_dir, f"screenshots/{block['value']}"), alt_text = block["alt_text"]))

    user = User.login(username, password)
    project = user.getProject(handle)

    # change draft to False once it's ready
    print(f"posting chost '{chost['title']}'")
    newPost = project.post(chost["title"], blocks, tags = chost["tags"], draft=False)

if __name__ == "__main__":
    print("\n\nSTARTING NEW SESSION")
    print(datetime.now())
    print("\n\n\n")
    if len(sys.argv) != 3:
        print(f"""Usage: python {sys.argv[0]} [creds file] [link file] -- the link file is a simple text file that contains the path to the current base dir
when uploading the next chosts json, simply put the path to the new folder into this file and the program
will read it and load the new files once it has completed posting the old chosts.""")
        exit(1)

    with open(sys.argv[1]) as f:
        creds = json.load(f)
        username = creds["username"]
        password = creds["password"]
        handle = creds["handle"]

    link_file = sys.argv[2]

    base_dir = ""

    s = sched.scheduler(time.time, time.sleep)


    had_current_chost = True
    while had_current_chost:

        with open(link_file, 'r') as f:
            base_dir = f.readline().replace('\n', '')

        had_current_chost = False

        chosts = None
        with open(os.path.join(base_dir, "chosts.json"), 'r') as f:
            chosts = json.load(f)

        assert(len(chosts) > 0)

        for chost in chosts:
            # determine post time
            post_time_stamp = datetime.strptime(chost["post_time"], "%Y-%m-%d %H:%M:%S %Z").replace(tzinfo=pytz.utc).timestamp()

            if post_time_stamp < time.time():
                # we already missed the slot. continue
                continue

            had_current_chost = True

            print(f"scheduling chost '{chost['title']}'")
            s.enterabs(post_time_stamp, 0, post_chost, argument = (chost, base_dir))

        if not had_current_chost:
            print("Ran out of chosts :(")
            exit(1)

        s.run()
