import json
import os
import unittest
from collections import namedtuple

from nose.plugins.attrib import attr
from parameterized.parameterized import parameterized

from conans.client.tools.scm import Git, SVN
from conans.client.tools.win import get_cased_path
from conans.model.ref import ConanFileReference, PackageReference
from conans.model.scm import SCMData
from conans.test.utils.test_files import temp_folder
from conans.test.utils.tools import NO_SETTINGS_PACKAGE_ID, SVNLocalRepoTestCase, TestClient, \
    TestServer, create_local_git_repo
from conans.util.files import load, rmdir, save, to_file_bytes

base = '''
import os
from conans import ConanFile, tools

def get_svn_remote(path_from_conanfile):
    svn = tools.SVN(os.path.join(os.path.dirname(__file__), path_from_conanfile))
    return svn.get_remote_url()

class ConanLib(ConanFile):
    name = "lib"
    version = "0.1"
    short_paths = True
    scm = {{
        "type": "%s",
        "url": {url},
        "revision": "{revision}",
    }}

    def build(self):
        sf = self.scm.get("subfolder")
        path = os.path.join(sf, "myfile.txt") if sf else "myfile.txt"
        self.output.warn(tools.load(path))
'''

base_git = base % "git"
base_svn = base % "svn"


def _quoted(item):
    return '"{}"'.format(item)


@attr('git')
class GitSCMTest(unittest.TestCase):

    def setUp(self):
        self.ref = ConanFileReference.loads("lib/0.1@user/channel")
        self.client = TestClient()

    def _commit_contents(self):
        self.client.run_command("git init .")
        self.client.run_command('git config user.email "you@example.com"')
        self.client.run_command('git config user.name "Your Name"')
        self.client.run_command("git add .")
        self.client.run_command('git commit -m  "commiting"')

    def test_scm_other_type_ignored(self):
        conanfile = '''
from conans import ConanFile, tools

class ConanLib(ConanFile):
    name = "lib"
    version = "0.1"
    scm = ["Other stuff"]

    def build(self):
        self.output.writeln("scm: {}".format(self.scm))
'''
        self.client.save({"conanfile.py": conanfile})
        # nothing breaks
        self.client.run("create . user/channel")
        self.assertIn("['Other stuff']", self.client.out)

    def test_repeat_clone_changing_subfolder(self):
        tmp = '''
from conans import ConanFile, tools

class ConanLib(ConanFile):
    name = "lib"
    version = "0.1"
    scm = {{
        "type": "git",
        "url": "{url}",
        "revision": "{revision}",
        "subfolder": "onesubfolder"
    }}
'''
        path, commit = create_local_git_repo({"myfile": "contents"}, branch="my_release")
        conanfile = tmp.format(url=path, revision=commit)
        self.client.save({"conanfile.py": conanfile,
                          "myfile.txt": "My file is copied"})
        self.client.run("create . user/channel")
        conanfile = conanfile.replace('"onesubfolder"', '"othersubfolder"')
        self.client.save({"conanfile.py": conanfile})
        self.client.run("create . user/channel")
        folder = self.client.cache.package_layout(self.ref).source()
        self.assertIn("othersubfolder", os.listdir(folder))
        self.assertTrue(os.path.exists(os.path.join(folder, "othersubfolder", "myfile")))

    def test_auto_filesystem_remote_git(self):
        # https://github.com/conan-io/conan/issues/3109
        conanfile = base_git.format(directory="None", url=_quoted("auto"), revision="auto")
        repo = temp_folder()
        self.client.save({"conanfile.py": conanfile, "myfile.txt": "My file is copied"}, repo)
        with self.client.chdir(repo):
            self.client.run_command("git init .")
            self.client.run_command('git config user.email "you@example.com"')
            self.client.run_command('git config user.name "Your Name"')
            self.client.run_command("git add .")
            self.client.run_command('git commit -m  "commiting"')
        self.client.run_command('git clone "%s" .' % repo)
        self.client.run("export . user/channel")
        self.assertIn("WARN: Repo origin looks like a local path", self.client.out)
        os.remove(self.client.cache.package_layout(self.ref).scm_folder())
        self.client.run("install lib/0.1@user/channel --build")
        self.assertIn("lib/0.1@user/channel: Getting sources from url:", self.client.out)

    def test_auto_git(self):
        curdir = get_cased_path(self.client.current_folder).replace("\\", "/")
        conanfile = base_git.format(directory="None", url=_quoted("auto"), revision="auto")
        self.client.save({"conanfile.py": conanfile, "myfile.txt": "My file is copied"})
        create_local_git_repo(folder=self.client.current_folder)
        self.client.run("export . user/channel", assert_error=True)
        self.assertIn("Repo origin cannot be deduced", self.client.out)

        self.client.run_command('git remote add origin https://myrepo.com.git')

        # Create the package, will copy the sources from the local folder
        self.client.run("create . user/channel")
        sources_dir = self.client.cache.package_layout(self.ref).scm_folder()
        self.assertEqual(load(sources_dir), curdir)
        self.assertIn("Repo origin deduced by 'auto': https://myrepo.com.git", self.client.out)
        self.assertIn("Revision deduced by 'auto'", self.client.out)
        self.assertIn("Getting sources from folder: %s" % curdir, self.client.out)
        self.assertIn("My file is copied", self.client.out)

        # check blank lines are respected in replacement
        self.client.run("get lib/0.1@user/channel")
        self.assertIn("""}

    def build(self):""", self.client.out)

        # Export again but now with absolute reference, so no pointer file is created nor kept
        git = Git(curdir)
        self.client.save({"conanfile.py": base_git.format(url=_quoted(curdir),
                                                          revision=git.get_revision())})
        self.client.run("create . user/channel")
        sources_dir = self.client.cache.package_layout(self.ref).scm_folder()
        self.assertFalse(os.path.exists(sources_dir))
        self.assertNotIn("Repo origin deduced by 'auto'", self.client.out)
        self.assertNotIn("Revision deduced by 'auto'", self.client.out)
        self.assertIn("Getting sources from url: '%s'" % curdir, self.client.out)
        self.assertIn("My file is copied", self.client.out)

    def test_auto_subfolder(self):
        conanfile = base_git.replace('"revision": "{revision}"',
                                     '"revision": "{revision}",\n        '
                                     '"subfolder": "mysub"')
        conanfile = conanfile.replace("short_paths = True", "short_paths = False")
        conanfile = conanfile.format(directory="None", url=_quoted("auto"), revision="auto")
        self.client.save({"conanfile.py": conanfile, "myfile.txt": "My file is copied"})
        create_local_git_repo(folder=self.client.current_folder)
        self.client.run_command('git remote add origin https://myrepo.com.git')
        self.client.run("create . user/channel")

        ref = ConanFileReference.loads("lib/0.1@user/channel")
        folder = self.client.cache.package_layout(ref).source()
        self.assertTrue(os.path.exists(os.path.join(folder, "mysub", "myfile.txt")))
        self.assertFalse(os.path.exists(os.path.join(folder, "mysub", "conanfile.py")))

    def test_auto_conanfile_no_root(self):
        """
        Conanfile is not in the root of the repo: https://github.com/conan-io/conan/issues/3465
        """
        curdir = get_cased_path(self.client.current_folder).replace("\\", "/")
        conanfile = base_git.format(url=_quoted("auto"), revision="auto")
        self.client.save({"conan/conanfile.py": conanfile, "myfile.txt": "content of my file"})
        self._commit_contents()
        self.client.run_command('git remote add origin https://myrepo.com.git')

        # Create the package
        self.client.run("create conan/ user/channel")
        sources_dir = self.client.cache.package_layout(self.ref).scm_folder()
        self.assertEqual(load(sources_dir), curdir.replace('\\', '/'))  # Root of git is 'curdir'

    def test_deleted_source_folder(self):
        path, _ = create_local_git_repo({"myfile": "contents"}, branch="my_release")
        curdir = self.client.current_folder.replace("\\", "/")
        conanfile = base_git.format(url=_quoted("auto"), revision="auto")
        self.client.save({"conanfile.py": conanfile, "myfile.txt": "My file is copied"})
        create_local_git_repo(folder=self.client.current_folder)
        self.client.run_command('git remote add origin "%s"' % path.replace("\\", "/"))
        self.client.run("export . user/channel")

        new_curdir = temp_folder()
        self.client.current_folder = new_curdir
        # delete old source, so it will try to checkout the remote because of the missing local dir
        rmdir(curdir)
        self.client.run("install lib/0.1@user/channel --build", assert_error=True)
        self.assertIn("Getting sources from url: '%s'" % path.replace("\\", "/"), self.client.out)

    def test_excluded_repo_fies(self):
        conanfile = base_git.format(url=_quoted("auto"), revision="auto")
        conanfile = conanfile.replace("short_paths = True", "short_paths = False")
        path, _ = create_local_git_repo({"myfile": "contents",
                                         "ignored.pyc": "bin",
                                         ".gitignore": """
*.pyc
my_excluded_folder
other_folder/excluded_subfolder
""",
                                         "myfile.txt": "My file!",
                                         "my_excluded_folder/some_file": "hey Apple!",
                                         "other_folder/excluded_subfolder/some_file": "hey Apple!",
                                         "other_folder/valid_file": "!",
                                         "conanfile.py": conanfile}, branch="my_release")
        self.client.current_folder = path
        self.client.run_command('git remote add origin "%s"' % path.replace("\\", "/"))
        self.client.run("create . user/channel")
        self.assertIn("Copying sources to build folder", self.client.out)
        pref = PackageReference(ConanFileReference.loads("lib/0.1@user/channel"),
                                NO_SETTINGS_PACKAGE_ID)
        bf = self.client.cache.package_layout(pref.ref).build(pref)
        self.assertTrue(os.path.exists(os.path.join(bf, "myfile.txt")))
        self.assertTrue(os.path.exists(os.path.join(bf, "myfile")))
        self.assertTrue(os.path.exists(os.path.join(bf, ".git")))
        self.assertFalse(os.path.exists(os.path.join(bf, "ignored.pyc")))
        self.assertFalse(os.path.exists(os.path.join(bf, "my_excluded_folder")))
        self.assertTrue(os.path.exists(os.path.join(bf, "other_folder", "valid_file")))
        self.assertFalse(os.path.exists(os.path.join(bf, "other_folder", "excluded_subfolder")))

    def test_local_source(self):
        curdir = self.client.current_folder.replace("\\", "/")
        conanfile = base_git.format(url=_quoted("auto"), revision="auto")
        conanfile += """
    def source(self):
        self.output.warn("SOURCE METHOD CALLED")
"""
        self.client.save({"conanfile.py": conanfile, "myfile.txt": "My file is copied"})
        create_local_git_repo(folder=self.client.current_folder)
        self.client.save({"aditional_file.txt": "contents"})

        self.client.run("source . --source-folder=./source")
        self.assertTrue(os.path.exists(os.path.join(curdir, "source", "myfile.txt")))
        self.assertIn("SOURCE METHOD CALLED", self.client.out)
        # Even the not commited files are copied
        self.assertTrue(os.path.exists(os.path.join(curdir, "source", "aditional_file.txt")))
        self.assertIn("Getting sources from folder: %s" % curdir,
                      str(self.client.out).replace("\\", "/"))

        # Export again but now with absolute reference, so no pointer file is created nor kept
        git = Git(curdir.replace("\\", "/"))
        conanfile = base_git.format(url=_quoted(curdir.replace("\\", "/")),
                                    revision=git.get_revision())
        conanfile += """
    def source(self):
        self.output.warn("SOURCE METHOD CALLED")
"""
        self.client.save({"conanfile.py": conanfile,
                          "myfile2.txt": "My file is copied"})
        create_local_git_repo(folder=self.client.current_folder)
        self.client.run("source . --source-folder=./source2")
        # myfile2 is no in the specified commit
        self.assertFalse(os.path.exists(os.path.join(curdir, "source2", "myfile2.txt")))
        self.assertTrue(os.path.exists(os.path.join(curdir, "source2", "myfile.txt")))
        self.assertIn("Getting sources from url: '%s'" % curdir.replace("\\", "/"), self.client.out)
        self.assertIn("SOURCE METHOD CALLED", self.client.out)

    def test_local_source_subfolder(self):
        curdir = self.client.current_folder
        conanfile = base_git.replace('"revision": "{revision}"',
                                     '"revision": "{revision}",\n        '
                                     '"subfolder": "mysub"')
        conanfile = conanfile.format(url=_quoted("auto"), revision="auto")
        conanfile += """
    def source(self):
        self.output.warn("SOURCE METHOD CALLED")
"""
        self.client.save({"conanfile.py": conanfile, "myfile.txt": "My file is copied"})
        create_local_git_repo(folder=self.client.current_folder)

        self.client.run("source . --source-folder=./source")
        self.assertFalse(os.path.exists(os.path.join(curdir, "source", "myfile.txt")))
        self.assertTrue(os.path.exists(os.path.join(curdir, "source", "mysub", "myfile.txt")))
        self.assertIn("SOURCE METHOD CALLED", self.client.out)

    @parameterized.expand([
        ("local", "v1.0", True),
        ("local", None, False),
        ("https://github.com/conan-io/conan.git", "0.22.1", True),
        ("https://github.com/conan-io/conan.git", "c6cc15fa2f4b576bd70c9df11942e61e5cc7d746", False)])
    def test_shallow_clone_remote(self, remote, revision, is_tag):
        # https://github.com/conan-io/conan/issues/5570
        self.client = TestClient()

        # "remote" git
        if remote == "local":
            remote, rev = create_local_git_repo(tags=[revision] if is_tag else None,
                                                files={"conans/model/username.py": "foo"})
            if revision is None:  # Get the generated commit
                revision = rev

        # Use explicit URL to avoid local optimization (scm_folder.txt)
        conanfile = '''
import os
from conans import ConanFile, tools

class ConanLib(ConanFile):
    name = "lib"
    version = "0.1"
    scm = {"type": "git", "url": "%s", "revision": "%s"}

    def build(self):
        assert os.path.exists(os.path.join(self.build_folder, "conans", "model", "username.py"))
''' % (remote, revision)
        self.client.save({"conanfile.py": conanfile})
        self.client.run("create . user/channel")

    def test_install_checked_out(self):
        test_server = TestServer()
        self.servers = {"myremote": test_server}
        self.client = TestClient(servers=self.servers, users={"myremote": [("lasote", "mypass")]})

        curdir = self.client.current_folder.replace("\\", "/")
        conanfile = base_git.format(url=_quoted("auto"), revision="auto")
        self.client.save({"conanfile.py": conanfile, "myfile.txt": "My file is copied"})
        create_local_git_repo(folder=self.client.current_folder)
        cmd = 'git remote add origin "%s"' % curdir
        self.client.run_command(cmd)
        self.client.run("export . lasote/channel")
        self.client.run("upload lib* -c")

        # Take other client, the old client folder will be used as a remote
        client2 = TestClient(servers=self.servers, users={"myremote": [("lasote", "mypass")]})
        client2.run("install lib/0.1@lasote/channel --build")
        self.assertIn("My file is copied", client2.out)

    def test_source_removed_in_local_cache(self):
        conanfile = '''
from conans import ConanFile, tools

class ConanLib(ConanFile):
    scm = {
        "type": "git",
        "url": "auto",
        "revision": "auto",
    }

    def build(self):
        contents = tools.load("myfile")
        self.output.warn("Contents: %s" % contents)

'''
        path, _ = create_local_git_repo({"myfile": "contents", "conanfile.py": conanfile},
                                        branch="my_release")
        self.client.current_folder = path
        self.client.run_command('git remote add origin https://myrepo.com.git')
        self.client.run("create . lib/1.0@user/channel")
        self.assertIn("Contents: contents", self.client.out)
        self.client.save({"myfile": "Contents 2"})
        self.client.run("create . lib/1.0@user/channel")
        self.assertIn("Contents: Contents 2", self.client.out)
        self.assertIn("Detected 'scm' auto in conanfile, trying to remove source folder",
                      self.client.out)

    def test_submodule(self):
        subsubmodule, _ = create_local_git_repo({"subsubmodule": "contents"})
        submodule, _ = create_local_git_repo({"submodule": "contents"}, submodules=[subsubmodule])
        path, commit = create_local_git_repo({"myfile": "contents"}, branch="my_release",
                                             submodules=[submodule])

        def _relative_paths(folder):
            submodule_path = os.path.join(
                folder,
                os.path.basename(os.path.normpath(submodule)))
            subsubmodule_path = os.path.join(
                submodule_path,
                os.path.basename(os.path.normpath(subsubmodule)))
            return submodule_path, subsubmodule_path

        # Check old (default) behaviour
        tmp = '''
from conans import ConanFile, tools

class ConanLib(ConanFile):
    name = "lib"
    version = "0.1"
    scm = {{
        "type": "git",
        "url": "{url}",
        "revision": "{revision}"
    }}
'''
        conanfile = tmp.format(url=path, revision=commit)
        self.client.save({"conanfile.py": conanfile})
        self.client.run("create . user/channel")

        ref = ConanFileReference.loads("lib/0.1@user/channel")
        folder = self.client.cache.package_layout(ref).source()
        submodule_path, _ = _relative_paths(folder)
        self.assertTrue(os.path.exists(os.path.join(folder, "myfile")))
        self.assertFalse(os.path.exists(os.path.join(submodule_path, "submodule")))

        # Check invalid value
        tmp = '''
from conans import ConanFile, tools

class ConanLib(ConanFile):
    name = "lib"
    version = "0.1"
    scm = {{
        "type": "git",
        "url": "{url}",
        "revision": "{revision}",
        "submodule": "{submodule}"
    }}
'''
        conanfile = tmp.format(url=path, revision=commit, submodule="invalid")
        self.client.save({"conanfile.py": conanfile})

        self.client.run("create . user/channel", assert_error=True)
        self.assertIn("Invalid 'submodule' attribute value in the 'scm'.",
                      self.client.out)

        # Check shallow
        conanfile = tmp.format(url=path, revision=commit, submodule="shallow")
        self.client.save({"conanfile.py": conanfile})
        self.client.run("create . user/channel")

        ref = ConanFileReference.loads("lib/0.1@user/channel")
        folder = self.client.cache.package_layout(ref).source()
        submodule_path, subsubmodule_path = _relative_paths(folder)
        self.assertTrue(os.path.exists(os.path.join(folder, "myfile")))
        self.assertTrue(os.path.exists(os.path.join(submodule_path, "submodule")))
        self.assertFalse(os.path.exists(os.path.join(subsubmodule_path, "subsubmodule")))

        # Check recursive
        conanfile = tmp.format(url=path, revision=commit, submodule="recursive")
        self.client.save({"conanfile.py": conanfile})
        self.client.run("create . user/channel")

        ref = ConanFileReference.loads("lib/0.1@user/channel")
        folder = self.client.cache.package_layout(ref).source()
        submodule_path, subsubmodule_path = _relative_paths(folder)
        self.assertTrue(os.path.exists(os.path.join(folder, "myfile")))
        self.assertTrue(os.path.exists(os.path.join(submodule_path, "submodule")))
        self.assertTrue(os.path.exists(os.path.join(subsubmodule_path, "subsubmodule")))

    def test_scm_bad_filename(self):
        # Fixes: #3500
        badfilename = "\xE3\x81\x82badfile.txt"
        path, _ = create_local_git_repo({"goodfile.txt": "good contents"}, branch="my_release")
        save(to_file_bytes(os.path.join(self.client.current_folder, badfilename)), "contents")
        self.client.run_command('git remote add origin "%s"' % path.replace("\\", "/"), cwd=path)

        conanfile = '''
import os
from conans import ConanFile, tools

class ConanLib(ConanFile):
    name = "lib"
    version = "0.1"
    scm = {
        "type": "git",
        "url": "auto",
        "revision": "auto"
    }

    def build(self):
        pass
'''
        self.client.current_folder = path
        self.client.save({"conanfile.py": conanfile})
        self.client.run("create . user/channel")

    def test_source_method_export_sources_and_scm_mixed(self):
        path, _ = create_local_git_repo({"myfile": "contents"}, branch="my_release")

        conanfile = '''
import os
from conans import ConanFile, tools

class ConanLib(ConanFile):
    name = "lib"
    version = "0.1"
    exports_sources = "file.txt"
    scm = {
        "type": "git",
        "url": "%s",
        "revision": "my_release",
        "subfolder": "src/nested"
    }

    def source(self):
        self.output.warn("SOURCE METHOD CALLED")
        assert(os.path.exists("file.txt"))
        assert(os.path.exists(os.path.join("src", "nested", "myfile")))
        tools.save("cosa.txt", "contents")

    def build(self):
        assert(os.path.exists("file.txt"))
        assert(os.path.exists("cosa.txt"))
        self.output.warn("BUILD METHOD CALLED")
''' % path
        self.client.save({"conanfile.py": conanfile, "file.txt": "My file is copied"})
        self.client.run("create . user/channel")
        self.assertIn("SOURCE METHOD CALLED", self.client.out)
        self.assertIn("BUILD METHOD CALLED", self.client.out)

    def scm_serialization_test(self):
        data = {"url": "myurl", "revision": "myrevision", "username": "myusername",
                "password": "mypassword", "type": "git", "verify_ssl": True,
                "subfolder": "mysubfolder"}
        conanfile = namedtuple("ConanfileMock", "scm")(data)
        scm_data = SCMData(conanfile)

        expected_output = '{"password": "mypassword", "revision": "myrevision",' \
                          ' "subfolder": "mysubfolder", "type": "git", "url": "myurl",' \
                          ' "username": "myusername"}'
        self.assertEqual(str(scm_data), expected_output)

    def test_git_delegated_function(self):
        conanfile = """
import os
from conans import ConanFile
from conans.client.tools.scm import Git

def get_revision():
    here = os.path.dirname(__file__)
    git = Git(here)
    return git.get_commit()

def get_url():
    def nested_url():
        here = os.path.dirname(__file__)
        git = Git(here)
        return git.get_remote_url()
    return nested_url()

class MyLib(ConanFile):
    name = "issue"
    version = "3831"
    scm = {'type': 'git', 'url': get_url(), 'revision': get_revision()}

"""
        self.client.save({"conanfile.py": conanfile})
        path, commit = create_local_git_repo(folder=self.client.current_folder)
        self.client.run_command('git remote add origin "%s"' % path.replace("\\", "/"))

        self.client.run("export . user/channel")
        ref = ConanFileReference.loads("issue/3831@user/channel")
        exported_conanfile = self.client.cache.package_layout(ref).conanfile()
        content = load(exported_conanfile)
        self.assertIn(commit, content)

    def test_delegated_python_code(self):
        client = TestClient()
        code_file = """
from conans.tools import Git
from conans import ConanFile

def get_commit(repo_path):
    git = Git(repo_path)
    return git.get_commit()

class MyLib(ConanFile):
    pass
"""
        client.save({"conanfile.py": code_file})
        client.run("export . tool/0.1@user/testing")

        conanfile = """import os
from conans import ConanFile, python_requires
from conans.tools import load
tool = python_requires("tool/0.1@user/testing")

class MyLib(ConanFile):
    scm = {'type': 'git', 'url': '%s', 'revision': tool.get_commit(os.path.dirname(__file__))}
    def build(self):
        self.output.info("File: {}".format(load("file.txt")))
""" % client.current_folder.replace("\\", "/")

        client.save({"conanfile.py": conanfile, "file.txt": "hello!"})
        path, commit = create_local_git_repo(folder=client.current_folder)
        client.run_command('git remote add origin "%s"' % path.replace("\\", "/"))

        client.run("export . pkg/0.1@user/channel")
        ref = ConanFileReference.loads("pkg/0.1@user/channel")
        exported_conanfile = client.cache.package_layout(ref).conanfile()
        content = load(exported_conanfile)
        self.assertIn(commit, content)


@attr('svn')
class SVNSCMTest(SVNLocalRepoTestCase):

    def setUp(self):
        self.ref = ConanFileReference.loads("lib/0.1@user/channel")
        self.client = TestClient()

    def _commit_contents(self):
        self.client.run_command("svn add *")
        self.client.run_command('svn commit -m  "commiting"')

    def test_scm_other_type_ignored(self):
        conanfile = '''
from conans import ConanFile, tools

class ConanLib(ConanFile):
    name = "lib"
    version = "0.1"
    scm = ["Other stuff"]

    def build(self):
        self.output.writeln("scm: {}".format(self.scm))
'''
        self.client.save({"conanfile.py": conanfile})
        # nothing breaks
        self.client.run("create . user/channel")
        self.assertIn("['Other stuff']", self.client.out)

    def test_repeat_clone_changing_subfolder(self):
        tmp = '''
from conans import ConanFile, tools

class ConanLib(ConanFile):
    name = "lib"
    version = "0.1"
    scm = {{
        "type": "svn",
        "url": "{url}",
        "revision": "{revision}",
        "subfolder": "onesubfolder"
    }}
'''
        project_url, rev = self.create_project(files={"myfile": "contents"})
        conanfile = tmp.format(url=project_url, revision=rev)
        self.client.save({"conanfile.py": conanfile,
                          "myfile.txt": "My file is copied"})
        self.client.run("create . user/channel")
        conanfile = conanfile.replace('"onesubfolder"', '"othersubfolder"')
        self.client.save({"conanfile.py": conanfile})
        self.client.run("create . user/channel")
        ref = ConanFileReference.loads("lib/0.1@user/channel")
        folder = self.client.cache.package_layout(ref).source()
        self.assertIn("othersubfolder", os.listdir(folder))
        self.assertTrue(os.path.exists(os.path.join(folder, "othersubfolder", "myfile")))

    def test_auto_filesystem_remote_svn(self):
        # SVN origin will never be a local path (local repo has at least protocol file:///)
        pass

    def test_auto_svn(self):
        conanfile = base_svn.format(directory="None", url=_quoted("auto"), revision="auto")
        project_url, _ = self.create_project(files={"conanfile.py": conanfile,
                                                    "myfile.txt": "My file is copied"})
        project_url = project_url.replace(" ", "%20")
        self.client.run_command('svn co "{url}" "{path}"'.format(url=project_url,
                                                                 path=self.client.current_folder))

        curdir = self.client.current_folder.replace("\\", "/")
        # Create the package, will copy the sources from the local folder
        self.client.run("create . user/channel")
        sources_dir = self.client.cache.package_layout(self.ref).scm_folder()
        self.assertEqual(load(sources_dir), curdir)
        self.assertIn("Repo origin deduced by 'auto': {}".format(project_url).lower(),
                      str(self.client.out).lower())
        self.assertIn("Revision deduced by 'auto'", self.client.out)
        self.assertIn("Getting sources from folder: %s" % curdir, self.client.out)
        self.assertIn("My file is copied", self.client.out)

        # Export again but now with absolute reference, so no pointer file is created nor kept
        svn = SVN(curdir)
        self.client.save({"conanfile.py": base_svn.format(url=_quoted(svn.get_remote_url()),
                                                          revision=svn.get_revision())})
        self.client.run("create . user/channel")
        sources_dir = self.client.cache.package_layout(self.ref).scm_folder()
        self.assertFalse(os.path.exists(sources_dir))
        self.assertNotIn("Repo origin deduced by 'auto'", self.client.out)
        self.assertNotIn("Revision deduced by 'auto'", self.client.out)
        self.assertIn("Getting sources from url: '{}'".format(project_url).lower(),
                      str(self.client.out).lower())
        self.assertIn("My file is copied", self.client.out)

    def test_auto_subfolder(self):
        conanfile = base_svn.replace('"revision": "{revision}"',
                                     '"revision": "{revision}",\n        '
                                     '"subfolder": "mysub"')
        conanfile = conanfile.replace("short_paths = True", "short_paths = False")
        conanfile = conanfile.format(directory="None", url=_quoted("auto"), revision="auto")

        project_url, _ = self.create_project(files={"conanfile.py": conanfile,
                                                    "myfile.txt": "My file is copied"})
        project_url = project_url.replace(" ", "%20")
        self.client.run_command('svn co "{url}" "{path}"'.format(url=project_url,
                                                                 path=self.client.current_folder))
        self.client.run("create . user/channel")

        ref = ConanFileReference.loads("lib/0.1@user/channel")
        folder = self.client.cache.package_layout(ref).source()
        self.assertTrue(os.path.exists(os.path.join(folder, "mysub", "myfile.txt")))
        self.assertFalse(os.path.exists(os.path.join(folder, "mysub", "conanfile.py")))

    def test_auto_conanfile_no_root(self):
        """
        Conanfile is not in the root of the repo: https://github.com/conan-io/conan/issues/3465
        """
        curdir = self.client.current_folder
        conanfile = base_svn.format(url="get_svn_remote('..')", revision="auto")
        project_url, _ = self.create_project(files={"conan/conanfile.py": conanfile,
                                                    "myfile.txt": "My file is copied"})
        self.client.run_command('svn co "{url}" "{path}"'.format(url=project_url,
                                                                 path=self.client.current_folder))
        self.client.run("create conan/ user/channel")

        sources_dir = self.client.cache.package_layout(self.ref).scm_folder()
        self.assertEqual(load(sources_dir), curdir.replace('\\', '/'))  # Root of git is 'curdir'

    def test_deleted_source_folder(self):
        # SVN will always retrieve from 'remote'
        pass

    def test_excluded_repo_fies(self):
        conanfile = base_svn.format(url=_quoted("auto"), revision="auto")
        conanfile = conanfile.replace("short_paths = True", "short_paths = False")
        # SVN ignores pyc files by default:
        # http://blogs.collab.net/subversion/repository-dictated-configuration-day-3-global-ignores
        project_url, _ = self.create_project(files={"myfile": "contents",
                                                    "ignored.pyc": "bin",
                                                    # ".gitignore": "*.pyc\n",
                                                    "myfile.txt": "My file!",
                                                    "conanfile.py": conanfile})
        project_url = project_url.replace(" ", "%20")
        self.client.run_command('svn co "{url}" "{path}"'.format(url=project_url,
                                                                 path=self.client.current_folder))

        self.client.run("create . user/channel")
        self.assertIn("Copying sources to build folder", self.client.out)
        pref = PackageReference(ConanFileReference.loads("lib/0.1@user/channel"),
                                NO_SETTINGS_PACKAGE_ID)
        bf = self.client.cache.package_layout(pref.ref).build(pref)
        self.assertTrue(os.path.exists(os.path.join(bf, "myfile.txt")))
        self.assertTrue(os.path.exists(os.path.join(bf, "myfile")))
        self.assertTrue(os.path.exists(os.path.join(bf, ".svn")))
        self.assertFalse(os.path.exists(os.path.join(bf, "ignored.pyc")))

    def test_local_source(self):
        curdir = self.client.current_folder.replace("\\", "/")
        conanfile = base_svn.format(url=_quoted("auto"), revision="auto")
        conanfile += """
    def source(self):
        self.output.warn("SOURCE METHOD CALLED")
"""
        project_url, _ = self.create_project(files={"conanfile.py": conanfile,
                                                    "myfile.txt": "My file is copied"})
        project_url = project_url.replace(" ", "%20")
        self.client.run_command('svn co "{url}" "{path}"'.format(url=project_url,
                                                                 path=self.client.current_folder))
        self.client.save({"aditional_file.txt": "contents"})

        self.client.run("source . --source-folder=./source")
        self.assertTrue(os.path.exists(os.path.join(curdir, "source", "myfile.txt")))
        self.assertIn("SOURCE METHOD CALLED", self.client.out)
        # Even the not commited files are copied
        self.assertTrue(os.path.exists(os.path.join(curdir, "source", "aditional_file.txt")))
        self.assertIn("Getting sources from folder: %s" % curdir, self.client.out)

        # Export again but now with absolute reference, so no pointer file is created nor kept
        svn = SVN(curdir)
        conanfile = base_svn.format(url=_quoted(svn.get_remote_url()), revision=svn.get_revision())
        conanfile += """
    def source(self):
        self.output.warn("SOURCE METHOD CALLED")
"""
        self.client.save({"conanfile.py": conanfile,
                          "myfile2.txt": "My file is copied"})
        self._commit_contents()
        self.client.run("source . --source-folder=./source2")
        # myfile2 is no in the specified commit
        self.assertFalse(os.path.exists(os.path.join(curdir, "source2", "myfile2.txt")))
        self.assertTrue(os.path.exists(os.path.join(curdir, "source2", "myfile.txt")))
        self.assertIn("Getting sources from url: '{}'".format(project_url).lower(),
                      str(self.client.out).lower())
        self.assertIn("SOURCE METHOD CALLED", self.client.out)

    def test_local_source_subfolder(self):
        curdir = self.client.current_folder
        conanfile = base_svn.replace('"revision": "{revision}"',
                                     '"revision": "{revision}",\n        '
                                     '"subfolder": "mysub"')
        conanfile = conanfile.format(url=_quoted("auto"), revision="auto")
        conanfile += """
    def source(self):
        self.output.warn("SOURCE METHOD CALLED")
"""
        project_url, _ = self.create_project(files={"conanfile.py": conanfile,
                                                    "myfile.txt": "My file is copied"})
        project_url = project_url.replace(" ", "%20")
        self.client.run_command('svn co "{url}" "{path}"'.format(url=project_url,
                                                                 path=self.client.current_folder))

        self.client.run("source . --source-folder=./source")
        self.assertFalse(os.path.exists(os.path.join(curdir, "source", "myfile.txt")))
        self.assertTrue(os.path.exists(os.path.join(curdir, "source", "mysub", "myfile.txt")))
        self.assertIn("SOURCE METHOD CALLED", self.client.out)

    def test_install_checked_out(self):
        test_server = TestServer()
        self.servers = {"myremote": test_server}
        self.client = TestClient(servers=self.servers, users={"myremote": [("lasote", "mypass")]})

        conanfile = base_svn.format(url=_quoted("auto"), revision="auto")
        project_url, _ = self.create_project(files={"conanfile.py": conanfile,
                                                    "myfile.txt": "My file is copied"})
        project_url = project_url.replace(" ", "%20")
        self.client.run_command('svn co "{url}" "{path}"'.format(url=project_url,
                                                                 path=self.client.current_folder))
        self.client.run("export . lasote/channel")
        self.client.run("upload lib* -c")

        # Take other client, the old client folder will be used as a remote
        client2 = TestClient(servers=self.servers, users={"myremote": [("lasote", "mypass")]})
        client2.run("install lib/0.1@lasote/channel --build")
        self.assertIn("My file is copied", client2.out)

    def test_source_removed_in_local_cache(self):
        conanfile = '''
from conans import ConanFile, tools

class ConanLib(ConanFile):
    scm = {
        "type": "svn",
        "url": "auto",
        "revision": "auto",
    }

    def build(self):
        contents = tools.load("myfile")
        self.output.warn("Contents: %s" % contents)

'''
        project_url, _ = self.create_project(files={"myfile": "contents",
                                                    "conanfile.py": conanfile})
        project_url = project_url.replace(" ", "%20")
        self.client.run_command('svn co "{url}" "{path}"'.format(url=project_url,
                                                                 path=self.client.current_folder))

        self.client.run("create . lib/1.0@user/channel")
        self.assertIn("Contents: contents", self.client.out)
        self.client.save({"myfile": "Contents 2"})
        self.client.run("create . lib/1.0@user/channel")
        self.assertIn("Contents: Contents 2", self.client.out)
        self.assertIn("Detected 'scm' auto in conanfile, trying to remove source folder",
                      self.client.out)

    def test_submodule(self):
        # SVN has no submodules, may add something related to svn:external?
        pass

    def test_source_method_export_sources_and_scm_mixed(self):
        project_url, rev = self.create_project(files={"myfile": "contents"})
        project_url = project_url.replace(" ", "%20")
        self.client.run_command('svn co "{url}" "{path}"'.format(url=project_url,
                                                                 path=self.client.current_folder))

        conanfile = '''
import os
from conans import ConanFile, tools

class ConanLib(ConanFile):
    name = "lib"
    version = "0.1"
    exports_sources = "file.txt"
    scm = {{
        "type": "svn",
        "url": "{url}",
        "revision": "{rev}",
        "subfolder": "src"
    }}

    def source(self):
        self.output.warn("SOURCE METHOD CALLED")
        assert(os.path.exists("file.txt"))
        assert(os.path.exists(os.path.join("src", "myfile")))
        tools.save("cosa.txt", "contents")

    def build(self):
        assert(os.path.exists("file.txt"))
        assert(os.path.exists("cosa.txt"))
        self.output.warn("BUILD METHOD CALLED")
'''.format(url=project_url, rev=rev)
        self.client.save({"conanfile.py": conanfile, "file.txt": "My file is copied"})
        self.client.run("create . user/channel")
        self.assertIn("SOURCE METHOD CALLED", self.client.out)
        self.assertIn("BUILD METHOD CALLED", self.client.out)


@attr('svn')
class SCMSVNWithLockedFilesTest(SVNLocalRepoTestCase):

    def test_propset_own(self):
        """ Apply svn:needs-lock property to every file in the own working-copy
        of the repository """

        conanfile = base_svn.format(directory="None", url=_quoted("auto"), revision="auto")
        project_url, _ = self.create_project(files={"conanfile.py": conanfile})
        project_url = project_url.replace(" ", "%20")

        # Add property needs-lock to my own copy
        client = TestClient()
        client.run_command('svn co "{url}" "{path}"'.format(url=project_url,
                                                            path=client.current_folder))

        for item in ['conanfile.py', ]:
            client.run_command('svn propset svn:needs-lock yes {}'.format(item))
        client.run_command('svn commit -m "lock some files"')

        client.run("export . user/channel")
