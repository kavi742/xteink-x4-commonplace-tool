// Jenkinsfile — builds the Android APK whenever web/ changes.
// Place this file at the repo root. Jenkins Pipeline job points at this repo.

pipeline {
    agent any

    // Only trigger a full build when files under web/ change.
    // On manual runs (e.g. first run) the build always proceeds.
    options {
        skipDefaultCheckout(false)
        disableConcurrentBuilds()
    }

    environment {
        // Path baked into Dockerfile.jenkins
        ANDROID_HOME = '/opt/android-sdk'
        // Signing — optional. Set these as Jenkins credentials if you want
        // a release-signed APK. Comment out the sign stage if not needed yet.
        // KEYSTORE_FILE = credentials('android-keystore')
        // KEY_ALIAS     = credentials('android-key-alias')
        // KEY_PASSWORD  = credentials('android-key-password')
        // STORE_PASSWORD = credentials('android-store-password')
    }

    stages {
        stage('Install web deps') {
            steps {
                dir('web') {
                    sh 'npm ci'
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
                    // Installs Capacitor if not yet present, then copies
                    // web/build/ into android/app/src/main/assets/public/
                    sh 'npx cap sync android'
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

        // Uncomment after setting up signing credentials:
        // stage('Build APK (release)') {
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
            // Archive APK so it appears as a downloadable artifact in Jenkins UI
            archiveArtifacts artifacts: 'web/android/app/build/outputs/apk/debug/*.apk',
                             fingerprint: true
            echo 'APK archived — download from the Jenkins build page.'
        }
        failure {
            echo 'Build failed. Check the console output above.'
        }
    }
}
