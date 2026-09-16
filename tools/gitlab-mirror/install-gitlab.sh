#!/bin/bash
# Install GitLab CE (omnibus) in CT 115. Idempotent enough to re-run.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export EXTERNAL_URL="http://10.11.11.115"
# the Debian template has no generated locale; initdb needs a UTF-8 one
export LANG=C.UTF-8 LC_ALL=C.UTF-8
printf 'LANG=C.UTF-8\nLC_ALL=C.UTF-8\n' > /etc/default/locale

apt-get update
apt-get install -y curl openssh-server ca-certificates perl tzdata gnupg git

if [ ! -f /etc/apt/sources.list.d/gitlab_gitlab-ce.list ]; then
  curl -fsSL https://packages.gitlab.com/install/repositories/gitlab/gitlab-ce/script.deb.sh | bash
fi

# Debian 13 (trixie) may not be in packages.gitlab.com yet; fall back to bookworm packages.
if ! apt-cache policy gitlab-ce | grep -q Candidate: || apt-cache policy gitlab-ce | grep -q 'Candidate: (none)'; then
  sed -i 's/trixie/bookworm/g' /etc/apt/sources.list.d/gitlab_gitlab-ce.list
  apt-get update
fi
apt-cache policy gitlab-ce | head -3

apt-get install -y gitlab-ce

# Sensible defaults for an 8 GB / 4-core box that is not public-facing.
cat > /etc/gitlab/gitlab.rb.d-cruise.rb <<'RB'
RB
python3 - <<'PY'
import re, pathlib
p = pathlib.Path('/etc/gitlab/gitlab.rb')
s = p.read_text()
block = '''
# --- cruise instance tuning (added by install-gitlab.sh) ---
puma['worker_processes'] = 2
sidekiq['concurrency'] = 10
prometheus_monitoring['enable'] = false
postgresql['shared_buffers'] = "512MB"
gitlab_rails['gitlab_shell_ssh_port'] = 22
letsencrypt['enable'] = false
package['modify_kernel_parameters'] = false   # unprivileged LXC cannot sysctl
'''
if 'cruise instance tuning' not in s:
    p.write_text(s + block)
PY
rm -f /etc/gitlab/gitlab.rb.d-cruise.rb

gitlab-ctl reconfigure
gitlab-ctl status
echo "=== initial root password ==="
cat /etc/gitlab/initial_root_password | grep -v '^#' | grep Password
echo "=== INSTALL DONE ==="
