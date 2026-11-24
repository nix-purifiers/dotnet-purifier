# SPDX-License-Identifier: MIT
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

repository=Path(sys.argv[1])
poms=list(repository.rglob('*.pom'))
assert len(poms)==1
pom=ET.parse(poms[0]).getroot()
ns={'m':'http://maven.apache.org/POM/4.0.0'}
coordinate=':'.join(pom.find('m:'+k,ns).text for k in ['groupId','artifactId','version'])
project=Path.cwd()/'gradle-consumer'
project.mkdir()
report=project/'resolved.txt'
(project/'config.json').write_text(json.dumps({'repository':str(repository),'coordinate':coordinate,'report':str(report)}))
(project/'settings.gradle').write_text("rootProject.name='ordinary-dotnet-webjar-consumer'\n")
(project/'build.gradle').write_text('''def selected = new groovy.json.JsonSlurper().parse(file('config.json'))
repositories { maven { url=uri(selected.repository); metadataSources { mavenPom(); artifact() } } }
configurations { assets }
dependencies { assets(selected.coordinate) }
tasks.register('resolveAssets') { doLast { new File(selected.report).text=configurations.assets.singleFile.canonicalPath } }
''')
# Java's user.home need not follow HOME. Keep Gradle's native extraction and
# user caches inside this disposable consumer project, including on Darwin.
gradle_home=project/'gradle-user-home'
gradle_home.mkdir()
environment=os.environ.copy()
environment['GRADLE_USER_HOME']=str(gradle_home)
diagnostics=project/'cache-diagnostics.gradle'
diagnostics.write_text('''def nativeOverride = System.getProperty('org.gradle.native.dir')
if (nativeOverride == null) { nativeOverride = System.getenv('org.gradle.native.dir') }
def nativeDirectory = nativeOverride == null ? new File(gradle.gradleUserHomeDir, 'native') : new File(nativeOverride)
println('dotnet-gradle-cache-runtime: ' + groovy.json.JsonOutput.toJson([
    gradleUserHome: gradle.gradleUserHomeDir.absolutePath,
    configuredNativeDirectory: nativeDirectory.absolutePath,
    nativeDirectoryOverride: nativeOverride,
    javaHome: System.getProperty('java.home'),
    userHome: System.getProperty('user.home'),
    temporaryDirectory: System.getProperty('java.io.tmpdir')
]))
''')
print('dotnet-gradle-cache-selection: '+json.dumps({
    'requestedGradleUserHome': str(gradle_home),
    'directoryExists': gradle_home.is_dir(),
    'writeAccess': os.access(gradle_home,os.W_OK),
}),file=sys.stderr,flush=True)
subprocess.run(['gradle','--offline','--no-daemon','--console=plain','--stacktrace','--init-script',str(diagnostics),'resolveAssets'],cwd=project,env=environment,check=True,timeout=180)
resolved=Path(report.read_text())
assert hashlib.sha256(resolved.read_bytes()).digest()==hashlib.sha256(poms[0].with_suffix('.jar').read_bytes()).digest()
Path(sys.argv[2]).write_text(str(resolved))
Path(sys.argv[3]).write_text(coordinate+'\n')
