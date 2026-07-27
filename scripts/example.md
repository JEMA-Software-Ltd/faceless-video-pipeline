# The Lighthouse That Was Never Switched On

<!-- description: A worked example script showing the format this pipeline expects. Everything in it is invented — the island, the company, the committee and every figure. It exists to demonstrate structure, not to teach you anything true.

Replace this file's content with your own script. Keep the shape: one H1 title, a description comment, a tags comment, then plain paragraphs. -->

<!-- tags: example script, pipeline demo, format reference, placeholder -->

In eighteen ninety two, the Kessel Island Harbour Board approved the construction of a lighthouse at the northern tip of the island. The build took four years, cost eleven thousand guilders, and produced a tower ninety feet tall with a lamp visible, in theory, for nineteen miles.

The lamp was never lit. Not once, in a hundred and thirty years.

This is an example script, and every fact in it is invented. Kessel Island does not exist. Neither does the Harbour Board, the eleven thousand guilders, or the committee you are about to hear about. The purpose of this file is to show you what shape a script needs to be in before the pipeline will do anything useful with it.

Here is the shape. The first heading in the file becomes the video title. The description comment becomes the YouTube description. The tags comment becomes the tag list, on top of whatever channel-level tags you set in config.yaml. Everything after that is narration.

Each paragraph, separated by a blank line, becomes one chunk. A chunk is the unit the whole pipeline works in. It gets its own audio file, its own measured duration, its own set of search keywords and its own images. Keep paragraphs to a single idea and the imagery tends to track the narration better.

Short paragraphs are fine.

That one was four words long, and it will produce a chunk of roughly two seconds. Nothing merges or pads it. If you want a beat in the delivery, a short paragraph is how you ask for one.

Longer paragraphs are also fine, and in practice most of yours will be. A paragraph of sixty to ninety words lands somewhere between twenty five and forty seconds of narration, which is long enough to develop a point and short enough that the images keep changing. The pipeline picks one image per eight to twelve seconds of measured audio, so a longer paragraph simply gets more of them.

Now back to the lighthouse, briefly, because an example should demonstrate rather than only describe.

The reason the lamp was never lit is that the Harbour Board and the Northern Pilots Association disagreed about who would pay for the oil. The Board had budgeted for construction but not for operation. The Association argued that a lighthouse serves shipping generally and should therefore be funded generally. Both positions were reasonable. Neither side moved.

By the time anyone thought to resolve it, the shipping lane had shifted four miles east, and a lighthouse at the northern tip was no longer much use to anybody.

Write your own script in this format and save it as scripts slash the date, in year dash month dash day form, dot markdown. Then run the pipeline against that date. Your scripts stay on your machine — the repository ignores everything in the scripts folder except this example.
