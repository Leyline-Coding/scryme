// Jenkins pipeline (Kubernetes agents): backend tests with coverage, then a
// SonarQube quality-gate analysis. This COMPLEMENTS the GitHub Actions PR gate
// (lint + pytest + image build) — GitHub Actions stays required; this adds the
// SonarQube code-quality/coverage view and the quality gate.
//
// Prerequisites (already configured in this Jenkins via JCasC):
//   - Kubernetes plugin — agents are pods in the `jenkins` namespace (cloud "kubernetes").
//   - SonarQube Scanner plugin, server installation named 'SonarQube' pointing at
//     http://sonarqube-sonarqube.sonarqube:9000 with the 'sonarqube-token' credential.
//   - A multibranch job seeded from JCasC that reads this Jenkinsfile.
//   - For the Quality Gate stage: a SonarQube webhook -> https://jenkins.taylorcohron.me/sonarqube-webhook/
//
// The scan runs via `npx @sonar/scan` in a glibc node:20 container (the Alpine/musl
// sonar-scanner-cli image ships Node 18, which fails the analyzer bridge). @sonar/scan
// auto-provisions a JRE + scanner engine from the SonarQube server over :9000.

def podYaml = '''
apiVersion: v1
kind: Pod
spec:
  containers:
    - name: python
      image: python:3.12
      command: ["sleep"]
      args: ["infinity"]
    - name: node
      image: node:20
      command: ["sleep"]
      args: ["infinity"]
    - name: postgres
      image: postgres:16-alpine
      env:
        - { name: POSTGRES_USER, value: scryme }
        - { name: POSTGRES_PASSWORD, value: scryme }
        - { name: POSTGRES_DB, value: scryme_test }
'''

pipeline {
  agent {
    kubernetes {
      yaml podYaml
      defaultContainer 'python'
    }
  }

  options {
    timeout(time: 30, unit: 'MINUTES')
  }

  environment {
    SCRYME_ENVIRONMENT  = 'test'
    SCRYME_DATABASE_URL = 'postgresql+asyncpg://scryme:scryme@localhost:5432/scryme_test'
  }

  stages {
    stage('Backend tests + coverage') {
      steps {
        container('python') {
          sh '''
            cd backend
            python -m venv .venv && . .venv/bin/activate
            pip install --upgrade pip
            pip install -r requirements-dev.txt
            # Wait for the postgres sidecar (shared localhost) to accept connections.
            python - <<'PY'
import socket, time
for _ in range(60):
    try:
        socket.create_connection(("localhost", 5432), 1).close(); break
    except OSError:
        time.sleep(1)
else:
    raise SystemExit("postgres not ready")
PY
            pytest tests/   # pyproject addopts emit coverage.xml (Cobertura) in backend/
          '''
        }
      }
    }

    stage('SonarQube analysis') {
      steps {
        container('node') {
          withSonarQubeEnv('SonarQube') {
            // @sonar/scan reads sonar-project.properties (repo root) plus the
            // SONAR_HOST_URL + SONAR_TOKEN that withSonarQubeEnv injects; runs on
            // glibc Node 20 so the analyzer bridge starts. report-task.txt lands in
            // .scannerwork for the Quality Gate stage.
            sh 'npx --yes @sonar/scan'
          }
        }
      }
    }

    stage('Quality Gate') {
      steps {
        // Requires the SonarQube -> Jenkins webhook. For the very first run, before
        // the webhook exists, set abortPipeline:false (or comment this stage out).
        timeout(time: 5, unit: 'MINUTES') {
          waitForQualityGate abortPipeline: true
        }
      }
    }
  }
}
