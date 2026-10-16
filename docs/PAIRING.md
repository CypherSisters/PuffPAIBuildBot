# Pairing a device

Your dashboard only answers devices you have let in. Everything else on the
network is turned away, even though it can reach the port.

**First run.** `puffpai setup` finishes by printing a code like `7XK4-P2M9`,
and `puffpai start` (or the Start file) prints a fresh one each time it starts.
Open the dashboard on your phone or laptop, type the code on the screen it
shows you, and that device is in. It stays in for a year.

**If the code ran out.** Codes last one hour. Start your bot again (the Start
file, or `puffpai start`), or type `puffpai pair` on the computer running your
bot: it shows this bot's current code and, in that computer's own time, when it
stops working. It is the same line in every shell. The code comes from the
computer, not the network. (`npm run pair:code` in the `dashboard` folder still
works too.)

**Which bot is this page?** The bottom of the pairing screen shows the address
in your browser and the address the page reaches your bot at. If nothing told
the page where your bot is, it says so, and it tries the usual address
(`localhost:8000`). `puffpai start` always tells it.

**Each bot has its own code.** Two bots on one computer have two codes, and
a device you let into one is not let into the other. A bot keeps its pairing
in its own state folder (`pairing/pairing.json`). A copy of BuildBot that was
set up before this change keeps its paired devices: its first bot goes on
using the old `dashboard/data/pairing.json`, and any other bot gets its own.
If that older copy's web page says it cannot tell which pairing code is its
own, start the bot once (not only the web page): the bot writes down which bot
it is, and the page then finds its code.

**Adding or removing devices.** From one that is already paired, open `/pair`.
It shows the current code and lists everything you have let in; press Revoke
next to one and it stops working on its very next request. Lost the phone?
Revoke it from any other paired device.

**Rotating the code** stops new devices pairing with the old one and leaves
paired devices alone: the button on `/pair`, or `npm run pair:code -- --rotate`
in the `dashboard` folder.

**Worth knowing.** There is no TLS yet, so anyone watching your network traffic
can see the code and the cookie. Pairing keeps out the guest on your wifi and
the gadget in the corner. It is not protection against someone already on the
wire between you and the box.
