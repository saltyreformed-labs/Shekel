"""Tests for remediation Commit C-33: proxy trust tightening, network
topology hardening, and Nginx security-header defense-in-depth.

Covers audit findings:

    F-015  Nginx + Gunicorn trust all RFC 1918 ranges for proxy headers
    F-020  Flat shared "homelab" network exposes app to co-tenants
    F-063  Cloudflare Tunnel bypasses Nginx on the WAN path
    F-064  Shared Nginx vhost adds no security headers for Shekel
    F-129  UniFi + shared Nginx vhosts expand cross-service lateral movement
    F-156  ``server_tokens`` not explicitly disabled in Nginx

The tests are intentionally heavy on file-content assertions because
the artifacts under audit are configuration files, not Python objects.
A regression that loosens proxy trust or drops a defense-in-depth
header would silently weaken the WAN-path posture; pinning the exact
strings makes a future drift commit fail loudly at test time.

Two pieces of behaviour go beyond static content checks:

1. ``gunicorn.conf.py`` raises ``RuntimeError`` at import time when
   ``FORWARDED_ALLOW_IPS`` is unset or empty, and otherwise reflects
   the env-var value into the module's ``forwarded_allow_ips``
   global.  Validated by importing the module under controlled
   environments via ``runpy``.

2. ``deploy/nginx-shared/conf.d/shekel.conf`` references TLS
   certificate paths that exist only on the production host.  The
   bundled-mode test from C-32 already validates ``nginx -t`` against
   ``deploy/nginx-bundled/nginx.conf`` (which has the new headers
   and the tightened ``set_real_ip_from``); for the shared config
   we generate a self-signed cert in a tmp_path scratch directory
   and run ``nginx -t`` inside an ephemeral container.

The two subprocess-driven tests skip when Docker is not available on
the test host (matching the C-32 pattern in
``test_deploy_configs.py``).
"""
