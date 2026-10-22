# The image the cluster runs for the weekly publication refresh
# (MaastrichtU-IDS/services, ids3/projects/healthai-website/dev).
#
# Nothing here serves the website. The site is a static tree on GitHub Pages
# built from main; this image only regenerates content/publications.generated.yaml
# and pushes it back.
#
# Why each of these is present:
#   git, openssh-client  the job commits the result and pushes to main
#   netcat-openbsd       `nc -X connect` in the pod's ssh ProxyCommand. Port 22
#                        is blocked by university policy and by the cluster
#                        egress-proxy, so GitHub is reached over
#                        ssh.github.com:443 through squid. Without nc the first
#                        push fails looking exactly like a bad deploy key.
#   bash                 refresh_publications.sh is bash, not sh
#   ca-certificates      TLS to crisapi, ORCID, Crossref and GitHub
#
# PyYAML is the only third-party Python dependency; everything else the scripts
# use is stdlib.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      bash git openssh-client netcat-openbsd ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Matches runAsUser/runAsGroup in the CronJob. The HOME is what makes
# ~/.ssh/id_ed25519 resolve to the mounted deploy key.
RUN useradd --uid 10001 --create-home --home-dir /home/app --shell /bin/bash app

WORKDIR /work
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/ ./scripts/
COPY content/ ./content/

# The job writes cache/ and content/, and runs as a non-root user that does not
# own the image layers by default.
RUN mkdir -p /work/cache && chown -R 10001:10001 /work

USER 10001
ENV HOME=/home/app PYTHONUNBUFFERED=1
CMD ["/bin/bash", "scripts/refresh_publications.sh"]
