# --------------------------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for license information.
# --------------------------------------------------------------------------------------------

from __future__ import print_function
import re
import time

try:
    from urllib.parse import quote, urlparse
except ImportError:
    from urllib import quote  #pylint: disable=no-name-in-module
    from urlparse import urlparse  #pylint: disable=import-error
from vsts_info_provider import VstsInfoProvider
from continuous_delivery import ContinuousDelivery
from continuous_delivery.models import (AuthorizationInfo, AuthorizationInfoParameters, BuildConfiguration,
                                        CiArtifact, CiConfiguration, ProvisioningConfiguration,
                                        ProvisioningConfigurationSource, ProvisioningConfigurationTarget,
                                        SlotSwapConfiguration, SourceRepository)
from vsts_accounts import Account
from vsts_accounts.models import (AccountCreateInfoInternal)

# Use this class to setup or remove continuous delivery mechanisms for Azure web sites using VSTS build and release
class ContinuousDeliveryManager(object):
    def __init__(self, progress_callback):
        """
        Use this class to setup or remove continuous delivery mechanisms for Azure web sites using VSTS build and release
        :param progress_callback: method of the form func(count, total, message)
        """
        self._update_progress = progress_callback or self._skip_update_progress
        self._azure_info = _AzureInfo()
        self._repo_info = _RepositoryInfo()

    def get_vsts_app_id(self):
        """
        Use this method to get the 'resource' value for creating an Azure token to be used by VSTS
        :return: App id for VSTS
        """
        return '499b84ac-1321-427f-aa17-267ca6975798'

    def set_azure_web_info(self, resource_group_name, website_name, credentials,
                           subscription_id, subscription_name, tenant_id, webapp_location):
        """
        Call this method before attempting to setup continuous delivery to setup the azure settings
        :param resource_group_name:
        :param website_name:
        :param credentials:
        :param subscription_id:
        :param subscription_name:
        :param tenant_id:
        :param webapp_location:
        :return:
        """
        self._azure_info.resource_group_name = resource_group_name
        self._azure_info.website_name = website_name
        self._azure_info.credentials = credentials
        self._azure_info.subscription_id = subscription_id
        self._azure_info.subscription_name = subscription_name
        self._azure_info.tenant_id = tenant_id
        self._azure_info.webapp_location = webapp_location

    def set_repository_info(self, repo_url, branch, git_token):
        """
        Call this method before attempting to setup continuous delivery to setup the source control settings
        :param repo_url:
        :param branch:
        :param git_token:
        :return:
        """
        self._repo_info.url = repo_url
        self._repo_info.branch = branch
        self._repo_info.git_token = git_token

    def remove_continuous_delivery(self):
        """
        To be Implemented
        :return:
        """
        # TODO: this would be called by appservice web source-control delete
        return

    def setup_continuous_delivery(self, azure_deployment_slot, app_type, vsts_account_name, create_account,
                                  vsts_app_auth_token):
        """
        Use this method to setup Continuous Delivery of an Azure web site from a source control repository.
        :param azure_deployment_slot: the slot to use for deployment
        :param app_type: the type of app that will be deployed. i.e. AspNetWap, AspNetCore, etc.
        :param vsts_account_name:
        :param create_account:
        :param vsts_app_auth_token:
        :return: a message indicating final status and instructions for the user
        """

        branch = self._repo_info.branch or 'refs/heads/master'

        # Verify inputs before we start generating tokens
        source_repository, account_name, team_project_name = self._get_source_repository(self._repo_info.url,
            self._repo_info.git_token, branch, self._azure_info.credentials)
        self._verify_vsts_parameters(vsts_account_name, source_repository)
        vsts_account_name = vsts_account_name or account_name
        cd_project_name = team_project_name or self._azure_info.website_name
        account_url = 'https://{}.visualstudio.com'.format(quote(vsts_account_name))
        portalext_account_url = 'https://{}.portalext.visualstudio.com'.format(quote(vsts_account_name))

        account_created = False
        accountClient = Account('3.2-preview', None, self._azure_info.credentials)
        if create_account:
            # Try to create the account (already existing accounts are fine too)
            self._update_progress(0, 100, 'Creating or getting Team Services account information')
            properties = {}
            #TODO right now it is hard to match a random Azure region to a VSTS region
            #properties['Microsoft.VisualStudio.Services.Account.TfsAccountRegion'] = self._azure_info.webapp_location
            properties['Microsoft.VisualStudio.Services.Account.SignupEntryPoint'] = 'AzureCli'
            account_creation_parameters = AccountCreateInfoInternal(
                vsts_account_name, None, vsts_account_name, None, properties)
            creation_results = accountClient.create_account(account_creation_parameters, True)
            account_created = not creation_results.account_id == None
            if account_created:
                self._update_progress(5, 100, 'Team Services account created')
        else:
            # Verify that the account exists
            if not accountClient.account_exists(vsts_account_name):
                raise RuntimeError(
                    "'The Team Services url '{}' does not exist. Check the spelling and try again.".format(account_url))

        # Create ContinuousDelivery client
        cd = ContinuousDelivery('3.2-preview.1', portalext_account_url, self._azure_info.credentials)

        # Construct the config body of the continuous delivery call
        build_configuration = self._get_build_configuration(app_type, None)
        source = ProvisioningConfigurationSource('codeRepository', source_repository, build_configuration)
        auth_info = AuthorizationInfo('Headers', AuthorizationInfoParameters('Bearer ' + vsts_app_auth_token))
        slot_name = azure_deployment_slot or 'staging'
        slot_swap = None  # TODO SlotSwapConfiguration(slot_name)
        target = ProvisioningConfigurationTarget('azure', 'windowsAppService', 'production', 'Production',
                                                 self._azure_info.subscription_id,
                                                 self._azure_info.subscription_name, self._azure_info.tenant_id,
                                                 self._azure_info.website_name, self._azure_info.resource_group_name,
                                                 self._azure_info.webapp_location, auth_info, slot_swap)
        ci_config = CiConfiguration(CiArtifact(name=cd_project_name))
        config = ProvisioningConfiguration(None, source, [target], ci_config)

        # Configure the continuous deliver using VSTS as a backend
        response = cd.provisioning_configuration(config)
        if response.ci_configuration.result.status == 'queued':
            final_status = self._wait_for_cd_completion(cd, response)
            return self._get_summary(final_status, account_url, vsts_account_name, account_created, self._azure_info.subscription_id,
                                     self._azure_info.resource_group_name, self._azure_info.website_name)
        else:
            raise RuntimeError('Unknown status returned from provisioning_configuration: ' + response.ci_configuration.result.status)

    def _verify_vsts_parameters(self, cd_account, source_repository):
        # if provider is vsts and repo is not vsts then we need the account name
        if source_repository.type in ['Github', 'ExternalGit'] and not cd_account:
            raise RuntimeError('You must provide a value for cd-account since your repo-url is not a Team Services repository.')

    def _get_build_configuration(self, app_type, working_directory):
        build_configuration = None
        if app_type == 'AspNetWap':
            build_configuration = BuildConfiguration(app_type, working_directory)
        elif app_type == 'AspNetCore':
            build_configuration = BuildConfiguration(app_type, working_directory)
        elif app_type == 'NodeJSWithGulp':
            build_configuration = BuildConfiguration('NodeJS', working_directory, 'Gulp')
        elif app_type == 'NodeJSWithGrunt':
            build_configuration = BuildConfiguration('NodeJS', working_directory, 'Grunt')
        else:
            raise RuntimeError("The app_type '{}' was not understood. Accepted values: AspNetWap, AspNetCore, NodeJSWithGulp, NodeJSWithGrunt.")
        return build_configuration

    def _get_source_repository(self, uri, token, branch, cred):
        # Determine the type of repository (TfsGit, github, tfvc, externalGit)
        # Find the identifier and set the properties; default to externalGit
        type = 'ExternalGit'
        identifier = uri
        account_name = None
        team_project_name = None
        auth_info = None
        match = re.match(r'[htps]+\:\/\/(.+)\.visualstudio\.com.*\/_git\/(.+)', uri, re.IGNORECASE)
        if match:
            type = 'TfsGit'
            account_name = match.group(1)
            # we have to get the repo id as the identifier
            info = self._get_vsts_info(uri, cred)
            identifier = info.repository_info.id
            team_project_name = info.repository_info.project_info.name
        else:
            match = re.match(r'[htps]+\:\/\/github\.com\/(.+)', uri, re.IGNORECASE)
            if match:
                type = 'Github'
                identifier = match.group(1)
                auth_info = AuthorizationInfo('PersonalAccessToken', AuthorizationInfoParameters(None, token))
            else:
                match = re.match(r'[htps]+\:\/\/(.+)\.visualstudio\.com\/(.+)', uri, re.IGNORECASE)
                if match:
                    type = 'TFVC'
                    identifier = match.group(2)
                    account_name = match.group(1)
        sourceRepository = SourceRepository(type, identifier, branch, auth_info)
        return sourceRepository, account_name, team_project_name

    def _get_vsts_info(self, vsts_repo_url, cred):
        vsts_info_client = VstsInfoProvider('3.2-preview', vsts_repo_url, cred)
        return vsts_info_client.get_vsts_info()

    def _wait_for_cd_completion(self, cd, response):
        # Wait for the configuration to finish and report on the status
        step = 5
        max = 100
        self._update_progress(step, max, 'Setting up Team Services continuous deployment')
        config = cd.get_provisioning_configuration(response.id)
        while config.ci_configuration.result.status == 'queued' or config.ci_configuration.result.status == 'inProgress':
            step += 5 if step + 5 < max else 0
            self._update_progress(step, max, 'Setting up Team Services continuous deployment (' + config.ci_configuration.result.status + ')')
            time.sleep(2)
            config = cd.get_provisioning_configuration(response.id)
        if config.ci_configuration.result.status == 'failed':
            self._update_progress(max, max, 'Setting up Team Services continuous deployment (FAILED)')
            raise RuntimeError(config.ci_configuration.result.status_message)
        self._update_progress(max, max, 'Setting up Team Services continuous deployment (SUCCEEDED)')
        return config

    def _get_summary(self, provisioning_configuration, account_url, account_name, account_created, subscription_id, resource_group_name, website_name):
        summary = '\n'
        if not provisioning_configuration: return None

        # Add the vsts account info
        if not account_created:
            summary += "The Team Services account '{}' was updated to handle the continuous delivery.\n".format(account_url)
        else:
            summary += "The Team Services account '{}' was created to handle the continuous delivery.\n".format(account_url)

        # Add the subscription info
        website_url = 'https://portal.azure.com/#resource/subscriptions/{}/resourceGroups/{}/providers/Microsoft.Web/sites/{}/vstscd'.format(
            quote(subscription_id), quote(resource_group_name), quote(website_name))
        summary += 'You can check on the status of the Azure web site deployment here:\n'
        summary += website_url + '\n'

        # setup the build url and release url
        build_url = ''
        release_url = ''
        if provisioning_configuration.ci_configuration and provisioning_configuration.ci_configuration.project:
            project_id = provisioning_configuration.ci_configuration.project.id
            if provisioning_configuration.ci_configuration.build_definition:
                build_url = '{}/{}/_build?_a=simple-process&definitionId={}'.format(
                    account_url, quote(project_id), quote(provisioning_configuration.ci_configuration.build_definition.id))
            if provisioning_configuration.ci_configuration.release_definition:
                release_url = '{}/{}/_apps/hub/ms.vss-releaseManagement-web.hub-explorer?definitionId={}&_a=releases'.format(
                    account_url, quote(project_id), quote(provisioning_configuration.ci_configuration.release_definition.id))

        return ContinuousDeliveryResult(account_created, account_url, resource_group_name,
                                        subscription_id, website_name, website_url, summary,
                                        build_url, release_url, provisioning_configuration)

    def _skip_update_progress(self, count, total, message):
        return


class _AzureInfo(object):
    def __init__(self):
        self.resource_group_name = None
        self.website_name = None
        self.credentials = None
        self.subscription_id = None
        self.subscription_name = None
        self.tenant_id = None
        self.webapp_location = None


class _RepositoryInfo(object):
    def __init__(self):
        self.url = None
        self.branch = None
        self.git_token = None

class ContinuousDeliveryResult(object):
    def __init__(self, account_created, account_url, resource_group, subscription_id, website_name, cd_url, message, build_url, release_url, final_status):
        self.vsts_account_created = account_created
        self.vsts_account_url = account_url
        self.vsts_build_def_url = build_url
        self.vsts_release_def_url = release_url
        self.azure_resource_group = resource_group
        self.azure_subscription_id = subscription_id
        self.azure_website_name = website_name
        self.azure_continuous_delivery_url = cd_url
        self.status = 'SUCCESS'
        self.status_message = message
        self.status_details = final_status
