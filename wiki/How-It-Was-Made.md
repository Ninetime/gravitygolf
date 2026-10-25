# How it was made

Nothing about the original protocol was documented, so it was all **reverse engineered** from the
game:
- static analysis of the game executable in Ghidra
- live memory inspection of the running game through RPCS3's PINE interface
- logging every request the game sends

The work was done **with the assistance of AI (Claude, by Anthropic)**, which helped with:
- disassembly and decompilation analysis
- working out the encryption and message formats
- writing and testing the server code
- debugging against the running game
- writing the documentation

Findings were checked against the real game at each step. [Protocol](Protocol.md) records what was
learned, including the addresses in the game executable where each piece of behaviour lives.
[Third-Party Changes](Third-Party-Changes.md) lists what had to change in the Horizon Medius server
for this game's older network library.
