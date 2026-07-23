// Jenkins pipeline (split agents): backend tests + coverage on the unRAID Docker agents,
// then a SonarQube quality-gate analysis in-cluster. COMPLEMENTS the GitHub Actions PR
// gate (lint + pytest + image build) — GitHub Actions stays required; this adds the
// SonarQube code-quality/coverage view and the quality gate.
//
// Why the split (mirrors the uvdesk-docker job):
//   * Backend tests are the heavy part (full pytest suite + Postgres + coverage), so they
//     run on the permanent unRAID VMs (label `docker` = unraid-docker / unraid-docker-02),
//     keeping the k3s cluster free. Each tool runs in a throwaway container against the
//     VM's LOCAL Docker daemon via the docker-workflow plugin, with a Postgres sidecar.
//   * The SonarQube stage runs IN-CLUSTER: the off-cluster unRAID agents cannot reach
//     SonarQube (the public URL sits behind Cloudflare Access + a cert-hostname mismatch),
//     but an in-cluster pod reaches it on the internal service URL that the 'SonarQube'
//     server installation (JCasC) points at. coverage.xml is handed over via stash/unstash.
//
// The scan runs in a glibc node:20 container (NOT the Alpine/musl sonar-scanner-cli image,
// which ships Node 18 and fails the SonarJS analyzer bridge — scryme analyzes desktop/src
// JS, so the bridge must start). `npx @sonar/scan` auto-provisions a JRE + scanner engine
// from the SonarQube server.
//
// Prerequisites (already in the urza-helm jenkins values.yaml / JCasC):
//   - docker-workflow plugin + the two `docker`-labelled permanent nodes, each with a
//     working local Docker daemon.
//   - SonarQube server installation named 'SonarQube' -> internal service URL, credential
//     'sonarqube-token'.
//   - SonarQube webhook -> https://jenkins.taylorcohron.me/sonarqube-webhook/ (Quality Gate).

def sonarPodYaml = '''
apiVersion: v1
kind: Pod
spec:
  containers:
    - name: node
      image: node:20
      command: ["sleep"]
      args: ["infinity"]
'''

pipeline {
  // No global agent: each stage picks its own (unRAID Docker vs in-cluster pod).
  agent none

  options {
    timeout(time: 30, unit: 'MINUTES')
  }

  environment {
    SCRYME_ENVIRONMENT = 'test'
    // Postgres runs as a linked sidecar under the alias `postgres` (see below), not localhost.
    SCRYME_DATABASE_URL = 'postgresql+asyncpg://scryme:scryme@postgres:5432/scryme_test'
  }

  stages {
    stage('Backend tests + coverage') {
      // Heavy stage -> unRAID VMs. Jenkins load-balances across unraid-docker / -02.
      agent { label 'docker' }
      steps {
        script {
          // Ephemeral Postgres sidecar; torn down automatically when the closure exits.
          docker.image('postgres:16-alpine').withRun(
            '-e POSTGRES_USER=scryme -e POSTGRES_PASSWORD=scryme -e POSTGRES_DB=scryme_test'
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
              '''
            }
          }
        }
        // Hand the coverage report to the in-cluster Sonar stage (different agent).
        stash name: 'coverage', includes: 'backend/coverage.xml'
      }
    }

    stage('SonarQube analysis') {
      // In-cluster pod: reaches SonarQube on the internal service URL + provides Node 20
      // for the SonarJS analyzer bridge.
      agent {
        kubernetes {
          yaml sonarPodYaml
          defaultContainer 'node'
        }
      }
      steps {
        // Declarative checks out SCM on this agent (backend/src + desktop/src); bring the
        // coverage.xml produced on the unRAID agent on top of it.
        unstash 'coverage'
        withSonarQubeEnv('SonarQube') {
          // @sonar/scan reads sonar-project.properties (repo root) plus the
          // SONAR_HOST_URL + SONAR_TOKEN that withSonarQubeEnv injects. report-task.txt
          // lands in .scannerwork for the Quality Gate below.
          // Tag each analysis with the app version (from backend/src/__init__.py) so the
          // SonarQube Activity view is versioned and the "Previous version" New Code model
          // (if selected) uses each release as a fresh new-code baseline.
          sh '''
            VERSION=$(grep '__version__' backend/src/__init__.py | cut -d'"' -f2)
            echo "scryme version for analysis: ${VERSION:-unknown}"
            npx --yes @sonar/scan -Dsonar.projectVersion="${VERSION:-0.0.0}"
          '''
        }
        // Runs on this in-cluster agent so it can reach SonarQube to poll the task status.
        // Requires the SonarQube -> Jenkins webhook. For the very first run, before the
        // webhook exists, set abortPipeline:false (or comment this out).
        timeout(time: 5, unit: 'MINUTES') {
          waitForQualityGate abortPipeline: true
        }
      }
    }
  }
}
