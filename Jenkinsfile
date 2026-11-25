// Jenkins pipeline (split agents): backend tests + coverage on Docker-capable agents, then a
// SonarQube quality-gate analysis in a Kubernetes pod. COMPLEMENTS the GitHub Actions PR gate
// (lint + pytest + image build) — GitHub Actions stays required; this adds the SonarQube
// code-quality/coverage view and the quality gate.
//
// Why two agents:
//   * Backend tests are the heavy part (full pytest suite + Postgres + coverage), so they run
//     on agents labelled `docker`. Each tool runs in a throwaway container against the agent's
//     local Docker daemon via the docker-workflow plugin, with a Postgres sidecar.
//   * The SonarQube stage runs in a pod next to SonarQube, because the `docker` agents are not
//     assumed to have a network route to it. coverage.xml is handed over via stash/unstash.
//
// The scan runs in a glibc node:24 container (NOT the Alpine/musl sonar-scanner-cli image, whose
// bundled Node is too old for the SonarJS analyzer bridge — scryme analyzes desktop/src JS, so
// the bridge must start). `npx @sonar/scan` auto-provisions a JRE + scanner engine from the
// SonarQube server; it is pinned to a version below (it needs Node >= 22.12) so a new scanner
// release cannot change the build unannounced. Bump the pin deliberately.
//
// What the Jenkins controller must provide (nothing here is specific to one installation):
//   - docker-workflow plugin + at least one agent labelled `docker` with a working local Docker
//     daemon; the Kubernetes plugin for the SonarQube stage.
//   - A SonarQube server installation named 'SonarQube' (Manage Jenkins > System) with its
//     server URL and token credential. withSonarQubeEnv() reads both from there — no SonarQube
//     or Jenkins address is written in this file.
//   - A webhook IN SONARQUBE (Administration > Configuration > Webhooks) pointing back at this
//     controller: <your Jenkins URL>/sonarqube-webhook/ — i.e. the controller's `JENKINS_URL`
//     ("Jenkins URL" under Manage Jenkins > System) followed by `sonarqube-webhook/`. That is
//     how waitForQualityGate learns the result. It is configured on the SonarQube side only;
//     this pipeline never reads the address. Without it the stage does not pass silently: it
//     waits out the 5-minute timeout below and fails the build.

def sonarPodYaml = '''
apiVersion: v1
kind: Pod
spec:
  containers:
    - name: node
      image: node:24
      command: ["sleep"]
      args: ["infinity"]
'''

pipeline {
  // No global agent: each stage picks its own (Docker agent vs Kubernetes pod).
  agent none

  options {
    // Headroom for self-hosted agents, which may run the suite slower than the GitHub
    // Actions runners. Still bounded so a genuine hang can't run forever.
    timeout(time: 45, unit: 'MINUTES')
  }

  environment {
    SCRYME_ENVIRONMENT = 'test'
    // Postgres runs as a linked sidecar under the alias `postgres` (see below), not localhost.
    SCRYME_DATABASE_URL = 'postgresql+asyncpg://scryme:scryme@postgres:5432/scryme_test'
  }

  stages {
    stage('Backend tests + coverage') {
      // Heavy stage -> any agent labelled `docker`.
      agent { label 'docker' }
      steps {
        script {
          // Ephemeral Postgres sidecar; torn down automatically when the closure exits.
          // fsync/synchronous_commit/full_page_writes OFF: this DB is thrown away at the
          // end of the build, so durability is worthless — and turning it off removes the
          // per-commit disk-flush that dominated runtime on the agent's storage (the suite is
          // very DB-write heavy). The second withRun arg is the container command; the
          // postgres image prepends `postgres` when it starts with `-`.
          docker.image('postgres:16-alpine').withRun(
            '-e POSTGRES_USER=scryme -e POSTGRES_PASSWORD=scryme -e POSTGRES_DB=scryme_test',
            '-c fsync=off -c synchronous_commit=off -c full_page_writes=off'
          ) { pg ->
            // Link the sidecar in as alias `postgres` so SCRYME_DATABASE_URL resolves.
            docker.image('python:3.12').inside("--link ${pg.id}:postgres") {
              sh '''
                cd backend
                python -m venv .venv && . .venv/bin/activate
                pip install --upgrade pip
                pip install -r requirements-dev.txt
                # Wait for the linked postgres sidecar to accept connections.
                python - <<'PY'
import socket, time
for _ in range(60):
    try:
        socket.create_connection(("postgres", 5432), 1).close(); break
    except OSError:
        time.sleep(1)
else:
    raise SystemExit("postgres not ready")
PY
                # COVERAGE_CORE=sysmon (Python 3.12 sys.monitoring) traces code that runs inside
                # SQLAlchemy's async greenlets; the default C tracer misses statements after an
                # await that crosses the greenlet boundary, under-reporting every async
                # route/service. Needed for accurate coverage in the SonarQube report.
                COVERAGE_CORE=sysmon pytest tests/   # pyproject addopts emit coverage.xml (Cobertura)
                # The Sonar scan runs on a SEPARATE agent (Kubernetes pod) whose workspace
                # path differs from this agent's, and coverage.py bakes THIS agent's absolute path
                # into the Cobertura <source>. Over there it resolves to nothing -> Sonar maps
                # 0% coverage. Rewrite <source> so SonarPython resolves each file to
                # backend/src/<...> (matching sonar.sources) against the scan's projectBaseDir.
                #
                # Which prefix is correct depends on coverage.py's version-dependent rooting:
                #   * coverage >= ~7.14: <source>=.../backend/src, filenames bare ("routes/x.py")
                #                        -> need <source>backend/src
                #   * older:             <source>=.../backend,     filenames "src/routes/x.py"
                #                        -> need <source>backend
                # Detect from the first class filename so a coverage bump can't silently
                # re-zero coverage.
                python - <<'PY'
import re
s = open("coverage.xml").read()
m = re.search(r'<class[^>]*filename="([^"]*)"', s)
if not m:
    raise SystemExit("no class filename in coverage.xml")
prefix = "backend" if m.group(1).startswith("src/") else "backend/src"
s = re.sub(r'<source>.*?</source>', "<source>%s</source>" % prefix, s, flags=re.S)
open("coverage.xml", "w").write(s)
print("rewrote coverage.xml <source> ->", prefix)
PY
              '''
            }
          }
        }
        // Hand the coverage report to the Sonar stage (different agent).
        stash name: 'coverage', includes: 'backend/coverage.xml'
      }
    }

    stage('SonarQube analysis') {
      // Kubernetes pod: can reach SonarQube, and provides a current Node LTS for the SonarJS
      // analyzer bridge.
      agent {
        kubernetes {
          yaml sonarPodYaml
          defaultContainer 'node'
        }
      }
      steps {
        // Declarative checks out SCM on this agent (backend/src + desktop/src); bring the
        // coverage.xml produced on the Docker agent on top of it.
        unstash 'coverage'
        withSonarQubeEnv('SonarQube') {
          // @sonar/scan reads sonar-project.properties (repo root) plus the
          // SONAR_HOST_URL + SONAR_TOKEN that withSonarQubeEnv injects. report-task.txt
          // lands in .scannerwork for the Quality Gate below.
          // Tag each analysis with the app version (from backend/src/__init__.py) so the
          // SonarQube Activity view is versioned and the "Previous version" New Code model
          // (if selected) uses each release as a fresh new-code baseline.
          //
          // Branch / pull-request analysis: SonarQube Community Build has ONE branch per project,
          // so by default every build (main, feature branches, PRs) overwrites the same record
          // and one PR's new issue turns the quality gate red for every build after it. Once the
          // server can analyse branches (Developer Edition, or the community branch plugin at a
          // version matching the server), set SONAR_BRANCH_ANALYSIS=true on the Jenkins
          // controller or job and each PR/branch gets its own analysis and gate. Leave it unset
          // until then: a Community server REJECTS these parameters and the scan fails.
          sh '''
            VERSION=$(grep '__version__' backend/src/__init__.py | cut -d'"' -f2)
            echo "scryme version for analysis: ${VERSION:-unknown}"
            SCOPE=""
            if [ "${SONAR_BRANCH_ANALYSIS:-false}" = "true" ]; then
              if [ -n "${CHANGE_ID:-}" ]; then
                SCOPE="-Dsonar.pullrequest.key=${CHANGE_ID} -Dsonar.pullrequest.branch=${CHANGE_BRANCH} -Dsonar.pullrequest.base=${CHANGE_TARGET}"
              elif [ "${BRANCH_NAME:-main}" != "main" ]; then
                SCOPE="-Dsonar.branch.name=${BRANCH_NAME}"
              fi
            fi
            echo "analysis scope: ${SCOPE:-main branch}"
            npx --yes @sonar/scan@5.1.0 -Dsonar.projectVersion="${VERSION:-0.0.0}" ${SCOPE}
          '''
        }
        // Runs on this agent so it can reach SonarQube to poll the task status. Requires the
        // SonarQube -> Jenkins webhook described at the top of this file. For the very first
        // run, before the webhook exists, set abortPipeline:false (or comment this out).
        timeout(time: 5, unit: 'MINUTES') {
          waitForQualityGate abortPipeline: true
        }
      }
    }
  }
}
