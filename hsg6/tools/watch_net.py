"""Poll HSG6's network-state object over PINE and log field changes (lobby join debugging)."""
import struct, sys, time
sys.path.insert(0, r"E:\IT\tools")
import rpcs3_ipc as r

e = r.RPCS3()
r32 = lambda a: struct.unpack(">I", e.read_bytes(a, 4))[0]
FIELDS = [0x280, 0x288, 0x28c, 0x294, 0x7c0, 0x7c4, 0x804, 0x80c, 0x824, 0x828, 0x830, 0x948, 0xecc]
last = None
FIELDS_EXTRA = None
out = open(sys.argv[1] if len(sys.argv) > 1 else "watch_net.log", "a", buffering=1)
while True:
    try:
        net = r32(r32(0xd956e4))
        vals = tuple(r32(net + f) for f in FIELDS)
        if vals != last:
            ts = time.strftime("%H:%M:%S") + f".{int(time.time()*1000)%1000:03d}"
            out.write(ts + " " + " ".join(f"{f:x}={v:x}" for f, v in zip(FIELDS, vals)) + "\n")
            if last is None or vals[0] != last[0]:
                out.write("   connectinfo " + e.read_bytes(net + 0xc, 0x9c).hex() + "\n")
            last = vals
    except Exception as ex:
        out.write(f"err {ex}\n"); time.sleep(1)
    time.sleep(0.01)
