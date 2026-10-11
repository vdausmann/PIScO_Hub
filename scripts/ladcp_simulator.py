"""Two simulated WorkHorse ADCPs for developing and testing the LADCP page.

    python -m scripts.ladcp_simulator [--ports 7001 7002] [--bottom 800] [--speed 1.0]
                                      [--protocol rfc2217|socket] [--baud 115200]

The first port is the down-looker, the second the up-looker. Point the
LADCP setup at rfc2217://127.0.0.1:7001 and :7002 with break `hardware`
(or socket://… with break `===` for --protocol socket). See
app/backend/ladcp/simulator.py for what is imitated.
"""
import argparse
import time

from app.backend.ladcp.simulator import Simulator


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ports", nargs=2, type=int, default=[7001, 7002], metavar=("DN", "UP"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--bottom", type=float, default=800.0, help="water depth, m")
    parser.add_argument("--speed", type=float, default=1.0, help="winch speed, m/s")
    parser.add_argument("--protocol", choices=["rfc2217", "socket"], default="rfc2217",
                        help="rfc2217 like the ship's serial server (default), or a raw TCP line")
    parser.add_argument("--baud", type=int, default=115200, help="the instruments' starting baud rate")
    parser.add_argument("--soak", type=float, default=60.0, help="seconds at 5 m before going down")
    args = parser.parse_args()
    sim = Simulator(ports=tuple(args.ports), host=args.host, bottom=args.bottom,
                    speed=args.speed, soak=args.soak, protocol=args.protocol,
                    baud=args.baud).start()
    print("down-looker:", sim.urls[0])
    print("up-looker:  ", sim.urls[1])
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        sim.stop()


if __name__ == "__main__":
    main()
