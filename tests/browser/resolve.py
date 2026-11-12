# SPDX-License-Identifier: MIT
import hashlib
import json
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
subprocess.run(['gradle','--offline','--no-daemon','--console=plain','resolveAssets'],cwd=project,check=True,timeout=180)
resolved=Path(report.read_text())
assert hashlib.sha256(resolved.read_bytes()).digest()==hashlib.sha256(poms[0].with_suffix('.jar').read_bytes()).digest()
Path(sys.argv[2]).write_text(str(resolved))
Path(sys.argv[3]).write_text(coordinate+'\n')
