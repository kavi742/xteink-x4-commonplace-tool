// Jenkinsfile — builds the Android APK whenever web/ changes.
// Jenkins Pipeline job: Pipeline script from SCM → Git → this repo.

pipeline {
    agent any

    options {
        skipDefaultCheckout(false)
        disableConcurrentBuilds()
    }

    triggers {
        // Poll GitHub every 5 minutes; fires a build only if new commits found.
        // (Webhooks from GitHub require a publicly reachable Jenkins URL —
        //  use Tailscale Funnel if you want true push triggers.)
        pollSCM('H/5 * * * *')
    }

    environment {
        ANDROID_HOME = '/opt/android-sdk'
        PATH         = "${env.ANDROID_HOME}/cmdline-tools/latest/bin:${env.ANDROID_HOME}/platform-tools:${env.PATH}"
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scmGit(
                    branches: [[name: '*/main']],
                    userRemoteConfigs: [[url: 'https://github.com/kavi742/xteink-x4-commonplace-tool.git']]
                )
            }
        }

        stage('Install web deps') {
            steps {
                dir('web') {
                    sh 'npm ci --ignore-engines'
                }
            }
        }

        stage('Build SvelteKit (static)') {
            steps {
                dir('web') {
                    sh 'npm run build'
                }
            }
        }

        stage('Capacitor sync') {
            steps {
                dir('web') {
                    // Bake the NPM Basic Auth creds into the APK's server URL so the
                    // WebView passes the Access List on xteink.ghostbird.duckdns.org.
                    // APP_BASIC_AUTH ('user:pass') is read from an env file kept on the
                    // Jenkins agent ($JENKINS_HOME/app-basic-auth.env, off git — NOT in
                    // this repo, since the SCM checkout would not contain it) and is
                    // injected into the URL by capacitor.config.ts at sync time.
                    sh '''
                        ENV_FILE="${JENKINS_HOME:-/var/jenkins_home}/app-basic-auth.env"
                        if [ -f "$ENV_FILE" ]; then
                            set -a; . "$ENV_FILE"; set +a
                        else
                            echo "WARNING: $ENV_FILE not found — APK will have NO Basic Auth creds and will 401 against the NPM Access List."
                        fi
                        npx cap sync android
                    '''
                }
            }
        }

        stage('Build APK (debug)') {
            steps {
                dir('web/android') {
                    sh './gradlew assembleDebug --no-daemon'
                }
            }
        }

        // Uncomment after configuring signing credentials in Jenkins:
        // stage('Build APK (release)') {
        //     environment {
        //         KEYSTORE_FILE   = credentials('android-keystore')
        //         KEY_ALIAS       = credentials('android-key-alias')
        //         KEY_PASSWORD    = credentials('android-key-password')
        //         STORE_PASSWORD  = credentials('android-store-password')
        //     }
        //     steps {
        //         dir('web/android') {
        //             sh '''
        //                 ./gradlew assembleRelease --no-daemon \
        //                     -Pandroid.injected.signing.store.file=$KEYSTORE_FILE \
        //                     -Pandroid.injected.signing.store.password=$STORE_PASSWORD \
        //                     -Pandroid.injected.signing.key.alias=$KEY_ALIAS \
        //                     -Pandroid.injected.signing.key.password=$KEY_PASSWORD
        //             '''
        //         }
        //     }
        // }
    }

    post {
        success {
            archiveArtifacts artifacts: 'web/android/app/build/outputs/apk/debug/*.apk',
                             fingerprint: true
            echo 'APK archived — download from the Jenkins build page.'
        }
        failure {
            echo 'Build failed. Check the console output above.'
        }
    }
}
