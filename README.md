# Conference Study Journal Builder

Builds a printable PDF study journal from General Conference talks: cover, table of
contents, numbered talk paragraphs on the left ~2/3 of each page, a column of journal
lines down the right ~1/3, footnotes after each talk, page numbers, and a back page
with a "Priestcraft Free" seal.

## Easiest way (no command line)
Double-click `Start Journal Maker (Windows).bat` or `Start Journal Maker (Mac).command`,
paste a link into the page that opens, and click **Create my journal**. See `START HERE.txt`.

**Mac:** if double-clicking the `.command` file doesn't work (downloaded files lose their
permission to run and are blocked by macOS), open Terminal, type `cd ` and drag the
Conference Journal Maker folder into the window, press Return, then run:

    xattr -cr . ; chmod +x "Start Journal Maker (Mac).command" ; bash "Start Journal Maker (Mac).command"

After that, double-clicking works. If macOS offers to install "command line developer
tools", click Install (that is how a Mac gets Python) and run the line again.

## Command-line setup
    pip install requests beautifulsoup4 reportlab

## Three kinds of pages you can give it
    # 1. A whole conference (cover, session divider pages, talks)
    python build_journal.py "https://www.churchofjesuschrist.org/study/general-conference/2026/10?lang=eng" --name "Your Name"

    # 2. Every talk by one speaker (no session pages; conference month/year shown on each talk)
    python build_journal.py "https://www.churchofjesuschrist.org/study/general-conference/speakers/dallin-h-oaks?lang=eng" --name "Your Name"

    # 3. Every talk on one topic (no session pages; conference month/year shown on each talk)
    python build_journal.py "https://www.churchofjesuschrist.org/study/general-conference/topics/atonement?lang=eng" --name "Your Name"

Speaker/topic journals open with a cover like "General Conference Talks by / Dallin H. Oaks"
or "General Conference Talks on / Atonement", with the number of talks and the range of years.
Talks are in the same order as the website (newest first); add `--oldest-first` to reverse.
A big collection downloads one talk per second or so and can run to hundreds of pages.

Downloads are saved as editable .txt files (e.g. speaker_dallin-h-oaks/) and re-running
skips talks already downloaded, so an interrupted download can simply be resumed.

Other options: `--folder` (where to save talks), a second argument for the output PDF name,
`--logo my_logo.png` (use your own image on the back page instead of the built-in seal).

## Build from a folder of text files
    python build_journal.py conference_2026_10 MyJournal.pdf

Talk file format:
    Title: The Talk Title
    Speaker: Elder Name
    Role: Of the Quorum of the Twelve Apostles
    Conference: October 2026          (shown on each talk in speaker/topic journals)

    A paragraph with a footnote marker.[1]

    ## Optional sub-heading

    ---footnotes---
    1. Reference.

(Leading numbers in file/folder names only set the order. *text* = italics.
A folder with talk files directly inside it, and no session sub-folders, is built as a
speaker/topic-style collection.)

For personal study use. Fonts: Liberation Serif is bundled in fonts/ (SIL Open Font License);
keep that folder next to the scripts.
