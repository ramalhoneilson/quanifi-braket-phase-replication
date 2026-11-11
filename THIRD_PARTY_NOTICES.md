# Third-party material

The replication script is derived from Quanifi's historical circuit builder and
Braket adapters, copyright (C) 2026 Neilson Ramalho, licensed under AGPL-3.0-only.
See LICENSE and the provenance section in README.md.

Runs copy the installed Qiskit `qasm/libs/stdgates.inc` into their artifacts,
including the committed sample results. Its retained header identifies it as
the OpenQASM 3 standard gate library, vendored from the OpenQASM project at
commit `4ca1d793833b24a1`, copyright 2017–2024 OpenQASM Contributors, under the
Apache License, Version 2.0. That file is reproduced without modification in
`stdgates.inc` and inserted as-is into `legacy.qasm3`. The Apache-2.0 license
text is provided in `licenses/Apache-2.0.txt`.

Dependencies are installed separately and retain their own licenses. No
third-party implementation packages are bundled with this repository.
