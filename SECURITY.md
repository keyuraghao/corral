# Security policy

## Reporting a vulnerability

Email the maintainer rather than opening a public issue. Include the version, the platform, and the steps to reproduce. You will get an acknowledgement within a few days.

## What Corral is for

Corral runs commands you supply on machines you own or are explicitly authorized to test. It exists for authorized work: your own cross-platform test matrix, and reproducing a published vulnerability against infrastructure you are permitted to test, inside a disposable sandbox. Do not use it to attack systems you do not own or have permission to test.

## Posture and guarantees

- The controller binds to `127.0.0.1` by default. Expose it on a routable interface only behind your own authentication and transport security.
- Docker jobs run in a disposable container that is network-isolated by default. A job opts into a network explicitly.
- The worker is a dumb executor. It runs what the controller sends. Run workers only against a controller you trust, and run the controller only where you decide what jobs it accepts.
- No telemetry and no phone-home. Corral never sends data anywhere except between your controller and your workers.

## The CVE-reproduction workload

The CVE-reproduction job template is for defensive work: confirming whether a given version is affected by a published advisory, checking that a patched version is not, and producing a write-up for defenders. It operates on one advisory at a time, in an isolated sandbox you control. It is not a tool for untargeted or unauthorized exploitation, and it is not designed to be used as one.
