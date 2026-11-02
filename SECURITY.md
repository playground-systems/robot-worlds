# Security reports

For suspected credential exposure, bundle path traversal, arbitrary execution, or actor access to privileged grading state, check the repository's [Security page](https://github.com/playground-systems/robot-worlds/security) for an enabled private reporting route. If none is available, open a public issue requesting a private contact without publishing exploit details or secrets. No response-time commitment is currently published.

Include the affected version, platform, smallest reproduction and observed impact. Share sanitized artifacts only. Do not include API keys, remote-policy tokens, private scene images or provider account data. If a credential has been exposed, revoke it through its issuer.

The server binds to localhost by default. It is a single-user development workbench, not an authenticated multi-tenant service. Keep it on a trusted machine. Python policies are executable code supplied by the user; they are not sandboxed. Generated scene patches are typed data applied locally, not arbitrary generated Python.

Exported worlds and reports preserve task text, physical state and images. Content hashes detect changed artifacts against their saved manifests; they do not attest against a host rewriting both data and metadata. Remote policies receive declared actor observations, including images, through the configured service. Review the service and the [remote protocol](docs/remote-policy.md) before sending private scene data.
