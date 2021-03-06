import sys
import os
from os.path import join, exists
import re
import shutil
import tempfile
from StringIO import StringIO
import urllib2

from invoke import task, run

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCE_DIR = join(ROOT_DIR, 'src')
TEST_DIR = join(ROOT_DIR, 'utest')
DIST_DIR = join(ROOT_DIR, 'dist')
BUILD_DIR = join(ROOT_DIR, 'build')
ROBOTIDE_PACKAGE = join(ROOT_DIR, 'src', 'robotide')
BUNDLED_ROBOT_DIR = join(ROBOTIDE_PACKAGE, 'lib', 'robot')
# MANIFEST = ROOT_DIR/'MANIFEST.in'

TEST_PROJECT_DIR = 'theproject'
TEST_LIBS_GENERATED = 10
# Set VERSION global variable
execfile('src/robotide/version.py')
FINAL_RELEASE = bool(re.match('^(\d*\.){1,2}\d*$', VERSION))
wxPythonDownloadUrl = \
    "http://sourceforge.net/projects/wxpython/files/wxPython/2.8.12.1/"


# Developemnt tasks
@task
def devel(args=''):
    """Start development version of RIDE."""
    _set_development_path()
    from robotide import main
    main(*args.split(','))


@task
def test(test_filter=''):
    """Run unit tests."""
    _remove_bytecode_files()
    from nose import run as noserun
    _set_development_path()
    additional_args = []
    if test_filter:
        additional_args.append(test_filter)
    result = noserun(defaultTest=TEST_DIR,
                     argv=['', '--m=^test_'] + additional_args)
    assert result is True


@task
def deps(upgrade=False):
    """Fetch and install development dependencies."""
    cmd = 'pip install -r requirements.txt'
    if upgrade:
        run('{} --upgrade'.format(cmd))
    else:
        run(cmd)


@task
def clean():
    """Clean bytecode files and remove `dist` and `build` directories."""
    _clean()


@task
def update_robot(version=''):
    """Update robot framework to specified commit or tag.

    By default, update to current master.
    This task also repackages RF under `robotide.robot` to avoid
    accidentally importing system installation.

    `git`, `grep` and `sed` must be installed
    """
    target = version if version else 'master'
    run('(cd ../robotframework && git fetch && git checkout {})'.format(target))
    rf_commit_hash = run('(cd ../robotframework && git rev-parse HEAD)').stdout
    run('rm -rf {}'.format(BUNDLED_ROBOT_DIR))
    run('cp -r ../robotframework/src/robot src/robotide/lib/')
    # Prevent .pyc matching grep expressions
    _clean()
    # `import robot` -> `from robotide.lib import robot`
    _run_sed_on_matching_files(
        'import robot',
        's/import robot/from robotide.lib import robot/')
    # `from robot.pkg import stuff` -> `from robotide.lib.robot.pkg import stuff`
    _run_sed_on_matching_files(
        'from robot\..* import',
        's/from robot\./from robotide.lib.robot./')
    # `from robot import stuff` -> `from robotide.lib.robot import stuff`
    _run_sed_on_matching_files(
        'from robot import',
        's/from robot import/from robotide.lib.robot import/')
    with open(join(ROBOTIDE_PACKAGE, 'lib', 'robot-commit'), 'w') as rf_version_file:
        rf_version_file.write('{}\n'.format(rf_commit_hash))
    _log('Updated bundled Robot Framework to version {}/{}'.format(
        target, rf_commit_hash))


@task
def generate_big_project(install=False, upgrade=False, args=''):
    """Generate big test data project to help perf testing."""
    _remove_bytecode_files()
    if install or upgrade:
        rfgen_url = \
            "https://raw.github.com/robotframework/Generator/master/rfgen.py"
        _log("Installing/upgrading rfgen.py from github.")
        f = open('rfgen.py', 'wb')
        f.write(urllib2.urlopen(rfgen_url).read())
        f.close()
        _log("Done.")

    _set_development_path()
    sys.path.insert(0, '.')

    try:
        import rfgen
        assert rfgen.main(args.split(','))
    except ImportError:
        _log("Error: Did not find 'rfgen' script or installation")
        _log("Use 'invoke generate_big_project --install'")


@task
def random_test():
    """Use rtest go_find_bugs.py to randomly test RIDE API."""
    _remove_bytecode_files()
    _set_development_path()
    sys.path.insert(0, '.')
    from rtest.go_find_some_bugs import main
    dir = tempfile.mkdtemp()
    try:
        assert main(dir)
    finally:
        shutil.rmtree(dir, ignore_errors=True)


# Installation and distribution tasks
@task
def version(version):
    """Set `version.py` to given version."""
    with open(join(ROBOTIDE_PACKAGE, 'version.py'), 'w') as version_file:
        version_file.write("""# Automatically generated by `tasks.py`.
VERSION = '%s'
""" % version)
    _log('Set version to %s' % version)


@task
def register():
    """Register current version to Python package index."""
    _run_setup('register')


@task
def install():
    """Install development version and dependencies."""
    try:
        import wxversion
    except ImportError:
        _log("""No wxPython installation detected!

Please install wxPython before running RIDE.
You can download wxPython 2.8.12.1 from {}
""".format(wxPythonDownloadUrl))
    _run_setup('install')


def _run_setup(cmd):
    run('python setup.py {}'.format(cmd))


def release_notes_plugin():
    changes = _download_and_format_issues()
    plugin_path = os.path.join(
        ROBOTIDE_PACKAGE, 'application', 'releasenotes.py')
    content = open(plugin_path).read().rsplit('RELEASE_NOTES =', 1)[0]
    content += 'RELEASE_NOTES = """\n%s"""\n' % changes
    open(plugin_path, 'w').write(content)


@task(pre=[clean],
      help={
          'release-notes': 'If enabled, release notes plugin will be updated'})
def sdist(release_notes=True, upload=False):
    """Creates source distribution with bundled dependencies."""
    if release_notes:
        release_notes_plugin()
    _run_setup('sdist{}'.format('' if not upload else ' upload'))
    _after_distribution()


@task(pre=[clean])
def wininst():
    """Creates Windows installer with bundled dependencies."""
    if os.sep != '\\':
        sys.exit('Windows installers may only be created in Windows')

    _run_setup('bdist_wininst')
    _after_distribution()


@task
def release_notes():
    """Download and format issues in markdown format."""
    issues = _get_issues()
    _log("""ID  | Type | Priority | Summary
--- | ---- | -------- | ------- """)
    for i in issues:
        parts = ('#{}'.format(i.number), _find_type(i), _find_priority(i),
                 i.title)
        _log(' | '.join(parts))


# Helper functions

def _clean(keep_dist=False):
    _remove_bytecode_files()
    if not keep_dist and exists(DIST_DIR):
        shutil.rmtree(DIST_DIR)
    if exists(BUILD_DIR):
        shutil.rmtree(BUILD_DIR)


def _remove_bytecode_files():
    for d in SOURCE_DIR, TEST_DIR:
        _remove_files_matching(d, '.*\.pyc')


def _remove_files_matching(directory, pattern):
    for root, dirs, files in os.walk(directory):
        for file in filter(lambda x: re.match(pattern, x), files):
            os.remove(join(root, file))


def _set_development_path():
    sys.path.insert(0, SOURCE_DIR)


def _run_sed_on_matching_files(pattern, sed_expression):
    run("grep -lr '{}' {} | xargs sed -i '' -e '{}'".format(
        pattern, BUNDLED_ROBOT_DIR, sed_expression))


def _after_distribution():
    _log('Created:')
    for path in os.listdir(DIST_DIR):
        _log(os.path.abspath(os.path.join(DIST_DIR, path)))
    _clean(keep_dist=True)


def _download_and_format_issues():
    try:
        from robot.utils import HtmlWriter, html_format
    except ImportError:
        sys.exit('creating release requires Robot Framework to be installed.')
    writer = HtmlWriter(StringIO())
    writer.element('h2', 'Release notes for %s' % VERSION)
    writer.start('table', attrs={'border': '1'})
    writer.start('tr')
    for header in ['ID', 'Type', 'Priority', 'Summary']:
        writer.element(
            'td', html_format('*{}*'.format(header)), escape=False)
    writer.end('tr')
    issues = _get_issues()
    base_url = 'http://github.com/robotframework/RIDE/issues/'
    for issue in issues:
        writer.start('tr')
        link_tmpl = '<a href="{}{}">Issue {}</a>'
        row = [link_tmpl.format(base_url, issue.number, issue.number),
               _find_type(issue),
               _find_priority(issue),
               issue.title]
        for cell in row:
            writer.element('td', cell, escape=False)
        writer.end('tr')
    writer.end('table')
    writer.element('p', 'Altogether %d issues.' % len(issues))
    return writer.output.getvalue()


def _get_issues():
    import getpass
    from github3 import login
    milestone = re.split('[ab-]', VERSION)[0]
    username = raw_input('Enter GitHub username for downloading issues: ')
    password = getpass.getpass(
        'Github password for {user}: '.format(user=username))
    gh = login(username, password=password)
    repo = gh.repository('robotframework', 'RIDE')
    milestone_number = _get_milestone(repo, milestone)
    if milestone_number is None:
        _log('milestone not found')
        sys.exit(1)
    issues = list(repo.iter_issues(milestone=milestone_number, state='closed'))
    issues.sort(cmp=_issue_sorter)
    return issues


def _issue_sorter(i1, i2):
    prio_mapping = {
        'critical': 0,
        'high': 1,
        'medium': 2,
        'low': 3
    }
    prio1, prio2 = _find_priority(i1), _find_priority(i2)
    return cmp(prio_mapping[prio1], prio_mapping[prio2])


def _find_type(issue):
    type_labels = [l.name for l in issue.iter_labels()
                   if l.name in ['enhancement', 'bug', 'task']]
    return type_labels[0] if type_labels else 'Unknown type'


def _find_priority(issue):
    prio_labels = [l.name for l in issue.iter_labels()
                   if l.name.startswith('prio')]
    return prio_labels[0][5:] if prio_labels else 'Unknown priority'


def _get_milestone(repo, milestone_title):
    existing_milestones = list(repo.iter_milestones())
    milestone = [m for m in existing_milestones if m.title == milestone_title]
    if milestone:
        return milestone[0].number
    return None


def _log(msg):
    print msg
