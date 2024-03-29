# Copyright: (c) 2019, Johnathan Kupferer <jkupfere@redhat.com>
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

DOCUMENTATION = """
    lookup: template
    author: Johnathan kupferer <jkupfere@redhat.com>
    version_added: "2.9"
    short_description: Resolve kubernetes resource definitions
    description:
    - Returns a list of kubernetes resources from files, templates, etc.
    options:
      _terms:
        description: list of template sources
"""

EXAMPLES = """
- name: show templating results
  debug:
    msg: "{{ lookup('k8s_resource_definitions', source_list) }}"
"""

RETURN = """
_raw:
   description: k8s resource definitions
"""

from copy import deepcopy
import os
import subprocess
import tempfile
import yaml

from ansible.errors import AnsibleError, AnsibleParserError
from ansible.module_utils._text import to_text
from ansible.plugins.lookup import LookupBase
from ansible.template import generate_ansible_template_vars
from ansible.utils.display import Display

display = Display()

class LookupModule(LookupBase):
    def from_definition(self, definition):
        if definition['kind'] == 'List' and definition['apiVersion'] == 'v1':
            return definition.get('items', [])
        else:
            return [definition]

    def from_file(self, filename, variables):
        lookupfile = self.find_file_in_search_path(variables, 'files', filename)
        if not lookupfile:
            raise AnsibleError('Unable to find file: {}'.format(filename))
        b_contents, show_data = self._loader._get_file_contents(lookupfile)
        contents = to_text(b_contents, errors='surrogate_or_strict')
        ret = []
        for yaml_document in yaml.safe_load_all(contents):
            ret.extend(self.from_definition(yaml_document))
        return ret

    def from_openshift_template(self, openshift_template, variables):
        command = ['oc','process']
        temp_path = None
        if 'file' in openshift_template:
            fd, temp_path = tempfile.mkstemp()

            filename = openshift_template['file']
            lookupfile = self.find_file_in_search_path(variables, 'files', filename)
            if not lookupfile:
                raise AnsibleError('Unable to find OpenShift template: {}'.format(filename))
            b_contents, show_data = self._loader._get_file_contents(lookupfile)
            contents = to_text(b_contents, errors='surrogate_or_strict')
            with open(fd, 'w') as f:
                f.write(contents)
            #oc process --local -f lookupfile
            command.extend(['--local', '-f', temp_path])
        elif 'name' in openshift_template:
            command.append(openshift_template['name'])

        for k, v in openshift_template.get('parameters', {}).items():
            command.extend(['-p', '{}={}'.format(k, v)])

        p = subprocess.Popen(
            command, cwd=self._loader.get_basedir(), shell=False,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE
        )
        (stdout, stderr) = p.communicate()

        if temp_path:
            os.unlink(temp_path)

        return self.from_definition(yaml.safe_load(stdout.decode("utf-8")))

    def from_template(self, template, variables):
        template_file = template['file']
        template_vars = template.get('vars', {})

        lookupfile = self.find_file_in_search_path(variables, 'templates', template_file)
        if not lookupfile:
            raise AnsibleError('Unable to find file: {}'.format(template_file))
        b_template_data, show_data = self._loader._get_file_contents(lookupfile)
        template_data = to_text(b_template_data, errors='surrogate_or_strict')

        # set jinja2 internal search path for includes
        searchpath = variables.get('ansible_search_path', [])
        if searchpath:
            # our search paths aren't actually the proper ones for jinja includes.
            # We want to search into the 'templates' subdir of each search path in
            # addition to our original search paths.
            newsearchpath = []
            for p in searchpath:
                newsearchpath.append(os.path.join(p, 'templates'))
                newsearchpath.append(p)
            searchpath = newsearchpath
        searchpath.insert(0, os.path.dirname(lookupfile))

        self._templar.environment.loader.searchpath = searchpath

        vars = deepcopy(variables)
        vars.update(generate_ansible_template_vars(lookupfile))
        vars.update(template_vars)
        self._templar.available_variables = vars

        res = self._templar.template(
            template_data, preserve_trailing_newlines=True,
            convert_data=True, escape_backslashes=False
        )
        ret = []
        for yaml_document in yaml.safe_load_all(res):
            ret.extend(self.from_definition(yaml_document))
        return ret

    def run(self, terms, variables=None, **kwargs):
        ret = []

        for term in terms:
            if 'definition' in term:
                ret.extend(self.from_definition(term['definition']))
            elif 'file' in term:
                ret.extend(self.from_file(term['file'], variables))
            elif 'openshift_template' in term:
                ret.extend(self.from_openshift_template(term['openshift_template'], variables))
            elif 'template' in term:
                ret.extend(self.from_template(term['template'], variables))
            else:
                raise AnsibleError('Unknown resource definition: {}'.format(term))

        return ret
