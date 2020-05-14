"""
 License terms and conditions for Gluu Cloud Native Edition:
 https://www.apache.org/licenses/LICENSE-2.0
"""
# TODO: Delete this script as soon as the kubernetes python client fixes CRD issue
from .installclient import install_kubernetes_client_11_0_0
import importlib

try:
    from .kubeapi import Kubernetes
except ImportError:
    install_kubernetes_client_11_0_0()
# End of section to be removed. TODO
import argparse
from pathlib import Path
import contextlib
import os
import shutil
import time
import errno
import socket
import subprocess
import base64
import sys
from ast import literal_eval
from .pycert import check_cert_with_private_key
from .kubeapi import Kubernetes
from .couchbase import Couchbase
from .prompt import Prompt
from .yamlparser import Parser, get_logger, exec_cmd
from .helm import Helm
# TODO: Remove the following as soon as the update secret is moved to backend
from .updatesecrets import modify_secret

# End of section to be removed. TODO

logger = get_logger("gluu-create        ")

# Local Deployments
local_ldap_minikube_folder = Path("./ldap/overlays/minikube/local-storage/")
local_jcr_minikube_folder = Path("./jackrabbit/overlays/minikube/local-storage/")
local_ldap_microk8s_folder = Path("./ldap/overlays/microk8s/local-storage/")
local_jcr_microk8s_folder = Path("./jackrabbit/overlays/microk8s/local-storage/")
# AWS
local_ldap_eks_folder = Path("./ldap/overlays/eks/local-storage/")
local_jcr_eks_folder = Path("./jackrabbit/overlays/eks/local-storage/")
dynamic_ldap_eks_folder = Path("./ldap/overlays/eks/dynamic-ebs/")
dynamic_jcr_eks_folder = Path("./jackrabbit/overlays/eks/dynamic-ebs/")
static_ldap_eks_folder = Path("./ldap/overlays/eks/static-ebs/")
static_jcr_eks_folder = Path("./jackrabbit/overlays/eks/static-ebs/")
# GCE
local_ldap_gke_folder = Path("./ldap/overlays/gke/local-storage/")
local_jcr_gke_folder = Path("./jackrabbit/overlays/gke/local-storage/")
dynamic_ldap_gke_folder = Path("./ldap/overlays/gke/dynamic-pd/")
dynamic_jcr_gke_folder = Path("./jackrabbit/overlays/gke/dynamic-pd/")
static_ldap_gke_folder = Path("./ldap/overlays/gke/static-pd/")
static_jcr_gke_folder = Path("./jackrabbit/overlays/gke/static-pd/")
# AZURE
local_ldap_azure_folder = Path("./ldap/overlays/azure/local-storage/")
local_jcr_azure_folder = Path("./jackrabbit/overlays/azure/local-storage/")
dynamic_ldap_azure_folder = Path("./ldap/overlays/azure/dynamic-dn/")
dynamic_jcr_azure_folder = Path("./jackrabbit/overlays/azure/dynamic-dn/")
static_ldap_azure_folder = Path("./ldap/overlays/azure/static-dn/")
static_jcr_azure_folder = Path("./jackrabbit/overlays/azure/static-dn/")


def subprocess_cmd(command):
    """Execute command"""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, shell=True)
    proc_stdout = process.communicate()[0].strip()
    return proc_stdout


def ssh_and_remove(key, user, node_ip, folder_to_be_removed):
    """Execute ssh command and remove directory"""
    subprocess_cmd("ssh -oStrictHostKeyChecking=no -i {} {}@{} sudo rm -rf {}"
                   .format(key, user, node_ip, folder_to_be_removed))


def check_port(host, port):
    """Check if ports are open"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        conn = sock.connect_ex((host, port))
        if conn == 0:
            # port is not available
            return False
        return True


def copy(src, dest):
    try:
        shutil.copytree(src, dest)
    except OSError as e:
        # If the error was caused because the source wasn't a directory
        if e.errno == errno.ENOTDIR:
            shutil.copy(src, dest)
        else:
            logger.error('Directory not copied. Error: {}'.format(e))


def copy_templates():
    entries = Path(
        os.path.join(os.path.dirname(__file__), "templates")
    )
    curdir = os.getcwd()
    for entry in entries.iterdir():
        dst = os.path.join(curdir, entry.name)
        if os.path.exists(dst):
            continue
        copy(entry, dst)


class App(object):
    def __init__(self, settings, timeout=300):
        self.kubernetes = Kubernetes()
        self.settings = settings
        self.timeout = timeout
        if self.settings["DEPLOYMENT_ARCH"] != "microk8s" and self.settings["DEPLOYMENT_ARCH"] != "minikube":
            for port in [80, 443]:
                port_available = check_port("0.0.0.0", port)
                if not port_available:
                    logger.error(f'Required port {port} is bind to another process')
                    raise SystemExit(1)

        self.kubectl = self.detect_kubectl
        self.output_yaml_directory, self.ldap_kustomize_yaml_directory, self.jcr_kustomize_yaml_directory \
            = self.set_output_yaml_directory
        self.config_yaml = str(self.output_yaml_directory.joinpath("config.yaml").resolve())
        self.ldap_yaml = str(self.output_yaml_directory.joinpath("ldap.yaml").resolve())
        self.jackrabbit_yaml = str(self.output_yaml_directory.joinpath("jackrabbit.yaml").resolve())
        self.persistence_yaml = str(self.output_yaml_directory.joinpath("persistence.yaml").resolve())
        self.oxauth_yaml = str(self.output_yaml_directory.joinpath("oxauth.yaml").resolve())
        self.oxtrust_yaml = str(self.output_yaml_directory.joinpath("oxtrust.yaml").resolve())
        self.gluu_upgrade_yaml = str(self.output_yaml_directory.joinpath("upgrade.yaml").resolve())
        self.oxshibboleth_yaml = str(self.output_yaml_directory.joinpath("oxshibboleth.yaml").resolve())
        self.oxpassport_yaml = str(self.output_yaml_directory.joinpath("oxpassport.yaml").resolve())
        self.key_rotate_yaml = str(self.output_yaml_directory.joinpath("key-rotation.yaml").resolve())
        self.cr_rotate_yaml = str(self.output_yaml_directory.joinpath("cr-rotate.yaml").resolve())
        self.oxd_server_yaml = str(self.output_yaml_directory.joinpath("oxd-server.yaml").resolve())
        self.casa_yaml = str(self.output_yaml_directory.joinpath("casa.yaml").resolve())
        self.radius_yaml = str(self.output_yaml_directory.joinpath("radius.yaml").resolve())
        self.update_lb_ip_yaml = str(self.output_yaml_directory.joinpath("updatelbip.yaml").resolve())
        self.gg_ui_yaml = str(self.output_yaml_directory.joinpath("gg-ui.yaml").resolve())
        self.adjust_yamls_for_fqdn_status = dict()
        self.gluu_secret = ""
        self.gluu_config = ""

    @property
    def detect_kubectl(self):
        """Detect kubectl command"""
        # TODO: Set alias microk8s.kubectl to kubectl

        if self.settings["DEPLOYMENT_ARCH"] == "microk8s":
            kubectl = "microk8s.kubectl"
        else:
            kubectl = "kubectl"
        return kubectl

    def analyze_storage_class(self, storageclass):
        parser = Parser(storageclass, "StorageClass")
        if self.settings["DEPLOYMENT_ARCH"] == "eks":
            parser["provisioner"] = "kubernetes.io/aws-ebs"
            parser["parameters"]["encrypted"] = "true"
            parser["parameters"]["type"] = self.settings["LDAP_VOLUME"]
            unique_zones = list(dict.fromkeys(self.settings["NODES_ZONES"]))
            parser["allowedTopologies"][0]["matchLabelExpressions"][0]["values"] = unique_zones
            parser.dump_it()
        elif self.settings["DEPLOYMENT_ARCH"] == "gke":
            parser["provisioner"] = "kubernetes.io/gce-pd"
            try:
                del parser["parameters"]["encrypted"]
            except KeyError:
                logger.info("Key not deleted as they are not found inside yaml.")
            parser["parameters"]["type"] = self.settings["LDAP_VOLUME"]
            unique_zones = list(dict.fromkeys(self.settings["NODES_ZONES"]))
            parser["allowedTopologies"][0]["matchLabelExpressions"][0]["values"] = unique_zones
            parser.dump_it()
        elif self.settings["DEPLOYMENT_ARCH"] == "aks":
            parser["provisioner"] = "kubernetes.io/azure-disk"
            try:
                del parser["parameters"]["encrypted"]
                del parser["parameters"]["type"]
            except KeyError:
                logger.info("Key not deleted as they are not found inside yaml.")
            parser["parameters"]["storageaccounttype"] = self.settings["LDAP_VOLUME"]
            unique_zones = list(dict.fromkeys(self.settings["NODES_ZONES"]))
            parser["allowedTopologies"][0]["matchLabelExpressions"][0]["values"] = unique_zones
            parser.dump_it()
        elif self.settings['DEPLOYMENT_ARCH'] == "microk8s":
            try:
                parser["provisioner"] = "microk8s.io/hostpath"
                del parser["allowedTopologies"]
                del parser["allowVolumeExpansion"]
                del parser["parameters"]
            except KeyError:
                logger.info("Key not deleted as they are not found inside yaml.")
            parser.dump_it()
        elif self.settings['DEPLOYMENT_ARCH'] == "minikube":
            try:
                parser["provisioner"] = "k8s.io/minikube-hostpath"
                del parser["allowedTopologies"]
                del parser["allowVolumeExpansion"]
                del parser["parameters"]
            except KeyError:
                logger.info("Key not deleted as they are not found inside yaml.")
            parser.dump_it()

    @property
    def set_output_yaml_directory(self):

        if self.settings["DEPLOYMENT_ARCH"] == "minikube":
            copy(local_ldap_microk8s_folder, local_ldap_minikube_folder)
            copy(local_jcr_microk8s_folder, local_jcr_minikube_folder)
            output_yamls_folder = Path("gluu_minikube_yamls")
            ldap_kustomize_yaml_directory = local_ldap_minikube_folder
            jcr_kustomize_yaml_directory = local_jcr_minikube_folder

        elif self.settings["DEPLOYMENT_ARCH"] == "eks":
            output_yamls_folder = Path("gluu_eks_yamls")
            if self.settings["APP_VOLUME_TYPE"] == 7:
                self.analyze_storage_class(dynamic_ldap_eks_folder.joinpath("storageclasses.yaml"))
                self.analyze_storage_class(dynamic_jcr_eks_folder.joinpath("storageclasses.yaml"))
                ldap_kustomize_yaml_directory = dynamic_ldap_eks_folder
                jcr_kustomize_yaml_directory = dynamic_jcr_eks_folder

            elif self.settings["APP_VOLUME_TYPE"] == 8:
                ldap_kustomize_yaml_directory = static_ldap_eks_folder
                jcr_kustomize_yaml_directory = static_jcr_eks_folder

            else:
                ldap_kustomize_yaml_directory = local_ldap_eks_folder
                jcr_kustomize_yaml_directory = local_jcr_eks_folder

        elif self.settings["DEPLOYMENT_ARCH"] == "gke":
            output_yamls_folder = Path("gluu_gke_yamls")
            if self.settings["APP_VOLUME_TYPE"] == 12:
                try:
                    shutil.rmtree(dynamic_ldap_gke_folder)
                except FileNotFoundError:
                    logger.info("Directory not found. Copying...")
                try:
                    shutil.rmtree(dynamic_jcr_gke_folder)
                except FileNotFoundError:
                    logger.info("Directory not found. Copying...")

                copy(dynamic_ldap_eks_folder, dynamic_ldap_gke_folder)
                copy(dynamic_jcr_eks_folder, dynamic_jcr_gke_folder)
                self.analyze_storage_class(dynamic_ldap_eks_folder.joinpath("storageclasses.yaml"))
                self.analyze_storage_class(dynamic_jcr_eks_folder.joinpath("storageclasses.yaml"))

                ldap_kustomize_yaml_directory = dynamic_ldap_eks_folder
                jcr_kustomize_yaml_directory = dynamic_jcr_eks_folder
            elif self.settings["APP_VOLUME_TYPE"] == 13:
                ldap_kustomize_yaml_directory = static_ldap_gke_folder
                jcr_kustomize_yaml_directory = static_jcr_gke_folder

            else:
                ldap_kustomize_yaml_directory = local_ldap_gke_folder
                jcr_kustomize_yaml_directory = local_jcr_gke_folder

        elif self.settings["DEPLOYMENT_ARCH"] == "aks":
            output_yamls_folder = Path("gluu_aks_yamls")
            if self.settings["APP_VOLUME_TYPE"] == 17:
                copy(dynamic_ldap_eks_folder, dynamic_ldap_azure_folder)
                copy(dynamic_jcr_eks_folder, dynamic_jcr_azure_folder)

                self.analyze_storage_class(dynamic_ldap_azure_folder.joinpath("storageclasses.yaml"))
                self.analyze_storage_class(dynamic_jcr_azure_folder.joinpath("storageclasses.yaml"))

                ldap_kustomize_yaml_directory = dynamic_ldap_azure_folder
                jcr_kustomize_yaml_directory = dynamic_ldap_azure_folder

            elif self.settings["APP_VOLUME_TYPE"] == 18:
                ldap_kustomize_yaml_directory = static_ldap_azure_folder
                jcr_kustomize_yaml_directory = static_jcr_azure_folder

            else:
                ldap_kustomize_yaml_directory = local_ldap_azure_folder
                jcr_kustomize_yaml_directory = local_jcr_azure_folder

        else:
            output_yamls_folder = Path("gluu_microk8s_yamls")
            ldap_kustomize_yaml_directory = local_ldap_microk8s_folder
            jcr_kustomize_yaml_directory = local_jcr_microk8s_folder

        if not output_yamls_folder.exists():
            os.mkdir(output_yamls_folder)
        return output_yamls_folder, ldap_kustomize_yaml_directory, jcr_kustomize_yaml_directory

    def adjust_fqdn_yaml_entries(self):
        if self.settings["IS_GLUU_FQDN_REGISTERED"] == "Y" \
                or self.settings["DEPLOYMENT_ARCH"] == "microk8s" or self.settings["DEPLOYMENT_ARCH"] == "minikube" \
                or self.settings["DEPLOYMENT_ARCH"] == "gke":
            for k, v in self.adjust_yamls_for_fqdn_status.items():
                parser = Parser(k, v)
                volume_mount_list = parser["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]
                volume_list = parser["spec"]["template"]["spec"]["volumes"]

                if k != self.cr_rotate_yaml and k != self.key_rotate_yaml and k != self.gluu_upgrade_yaml:
                    cm_parser = Parser(k, "ConfigMap")
                    try:
                        del cm_parser["data"]["LB_ADDR"]
                    except KeyError:
                        logger.info("Key not deleted as they are not found inside yaml.")
                    cm_parser.dump_it()
                    if self.settings["DEPLOYMENT_ARCH"] == "microk8s" or \
                            self.settings["DEPLOYMENT_ARCH"] == "minikube" or self.settings["DEPLOYMENT_ARCH"] == "gke":
                        parser["spec"]["template"]["spec"]["hostAliases"][0]["hostnames"] = [self.settings["GLUU_FQDN"]]
                        parser["spec"]["template"]["spec"]["hostAliases"][0]["ip"] = self.settings["HOST_EXT_IP"]
                    else:
                        try:
                            del parser["spec"]["template"]["spec"]["hostAliases"]
                        except KeyError:
                            logger.info("Key not deleted as they are not found inside yaml.")
                    try:
                        del parser["spec"]["template"]["spec"]["containers"][0]["command"]
                    except KeyError:
                        logger.info("Key not deleted as they are not found inside yaml.")

                    update_lb_ip_vm_index = next(
                        (index for (index, d) in enumerate(volume_mount_list) if d["name"] == "update-lb-ip"), None)
                    if update_lb_ip_vm_index is not None:
                        del volume_mount_list[update_lb_ip_vm_index]
                    volume_list = parser["spec"]["template"]["spec"]["volumes"]
                    update_lb_ip_v_index = next(
                        (index for (index, d) in enumerate(volume_list) if d["name"] == "update-lb-ip"), None)
                    if update_lb_ip_v_index is not None:
                        del volume_list[update_lb_ip_v_index]
                if k != self.oxd_server_yaml and self.settings["PERSISTENCE_BACKEND"] == "ldap":
                    couchbase_password_v_index = next(
                        (index for (index, d) in enumerate(volume_list) if d["name"] == "cb-pass"), None)
                    if couchbase_password_v_index is not None:
                        del volume_list[couchbase_password_v_index]
                    couchbase_crt_v_index = next(
                        (index for (index, d) in enumerate(volume_list) if d["name"] == "cb-crt"), None)
                    if couchbase_crt_v_index is not None:
                        del volume_list[couchbase_crt_v_index]
                    couchbase_password_vm_index = next(
                        (index for (index, d) in enumerate(volume_mount_list) if d["name"] == "cb-pass"), None)
                    if couchbase_password_vm_index is not None:
                        del volume_mount_list[couchbase_password_vm_index]
                    couchbase_crt_vm_index = next(
                        (index for (index, d) in enumerate(volume_mount_list) if d["name"] == "cb-crt"), None)
                    if couchbase_crt_vm_index is not None:
                        del volume_mount_list[couchbase_crt_vm_index]
                parser.dump_it()

        else:
            for k, v in self.adjust_yamls_for_fqdn_status.items():
                # oxAuth
                cm_parser = Parser(k, "ConfigMap")
                cm_parser["data"]["LB_ADDR"] = self.settings["LB_ADD"]
                cm_parser.dump_it()

                parser = Parser(k, v)
                # Check Couchbase entries
                if k != self.oxd_server_yaml and self.settings["PERSISTENCE_BACKEND"] == "ldap":
                    volume_mount_list = parser["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]
                    volume_list = parser["spec"]["template"]["spec"]["volumes"]

                    couchbase_password_v_index = next(
                        (index for (index, d) in enumerate(volume_list) if d["name"] == "cb-pass"), None)
                    if couchbase_password_v_index:
                        del volume_list[couchbase_password_v_index]
                    couchbase_crt_v_index = next(
                        (index for (index, d) in enumerate(volume_list) if d["name"] == "cb-crt"), None)
                    if couchbase_crt_v_index:
                        del volume_list[couchbase_crt_v_index]

                    couchbase_password_vm_index = next(
                        (index for (index, d) in enumerate(volume_mount_list) if d["name"] == "cb-pass"), None)
                    if couchbase_password_vm_index:
                        del volume_mount_list[couchbase_password_vm_index]
                    couchbase_crt_vm_index = next(
                        (index for (index, d) in enumerate(volume_mount_list) if d["name"] == "cb-crt"), None)
                    if couchbase_crt_vm_index:
                        del volume_mount_list[couchbase_crt_vm_index]

                if k != self.key_rotate_yaml and k != self.cr_rotate_yaml and k != self.gluu_upgrade_yaml:
                    parser["spec"]["template"]["spec"]["containers"][0]["command"] = \
                        ['/bin/sh', '-c', '/usr/bin/python /scripts/update-lb-ip.py & \n/app/scripts/entrypoint.sh\n']
                    volume_mount_list = parser["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]
                    parser["spec"]["template"]["spec"]["containers"][0]["volumeMounts"][len(volume_mount_list) - 1] = \
                        dict([('mountPath', '/scripts'), ('name', 'update-lb-ip')])
                    parser["spec"]["template"]["spec"]["hostAliases"][0]["hostnames"] = [self.settings["GLUU_FQDN"]]
                    parser["spec"]["template"]["spec"]["hostAliases"][0]["ip"] = self.settings["HOST_EXT_IP"]
                parser.dump_it()

    def prepare_alb(self):
        services = [self.oxauth_yaml, self.oxtrust_yaml, self.casa_yaml,
                    self.oxpassport_yaml, self.oxshibboleth_yaml]
        for service in services:
            if Path(service).is_file():
                service_parser = Parser(service, "Service")
                service_parser["spec"].update({"type": "NodePort"})
                service_parser["spec"]["ports"][0].update({"protocol": "TCP"})
                service_parser["spec"]["ports"][0].update({"targetPort": 8080})
                if service == self.oxpassport_yaml:
                    service_parser["spec"]["ports"][0]["targetPort"] = 8090
                service_parser.dump_it()
        ingress_parser = Parser("./alb/ingress.yaml", "Ingress")
        ingress_parser["spec"]["rules"][0]["host"] = self.settings["GLUU_FQDN"]
        ingress_parser["metadata"]["annotations"]["alb.ingress.kubernetes.io/certificate-arn"] = \
            self.settings["ARN_AWS_IAM"]
        if not self.settings["ARN_AWS_IAM"]:
            del ingress_parser["metadata"]["annotations"]["alb.ingress.kubernetes.io/certificate-arn"]

        for path in ingress_parser["spec"]["rules"][0]["http"]["paths"]:
            service_name = path["backend"]["serviceName"]
            if self.settings["ENABLE_CASA"] != "Y" and service_name == "casa":
                path_index = ingress_parser["spec"]["rules"][0]["http"]["paths"].index(path)
                del ingress_parser["spec"]["rules"][0]["http"]["paths"][path_index]

            if self.settings["ENABLE_OXSHIBBOLETH"] != "Y" and service_name == "oxshibboleth":
                path_index = ingress_parser["spec"]["rules"][0]["http"]["paths"].index(path)
                del ingress_parser["spec"]["rules"][0]["http"]["paths"][path_index]

            if self.settings["ENABLE_OXPASSPORT"] != "Y" and service_name == "oxpassport":
                path_index = ingress_parser["spec"]["rules"][0]["http"]["paths"].index(path)
                del ingress_parser["spec"]["rules"][0]["http"]["paths"][path_index]
        ingress_parser.dump_it()

    def update_kustomization_yaml(self):
        def update_image_name_tag(image_name_key, image_tag_key):
            parser["images"][0]["name"] = self.settings[image_name_key]
            parser["images"][0]["newTag"] = self.settings[image_tag_key]

        app_kustomization_yamls = ["./casa/base", "./config/base", "./cr-rotate/base", "./key-rotation/base",
                                   "./ldap/base", "./oxauth/base", "./oxd-server/base", "./oxpassport/base",
                                   "./oxshibboleth/base", "./oxtrust/base", "./persistence/base", "./radius/base",
                                   "./upgrade/base", "./jackrabbit/base", "./gluu-gateway-ui/base"]
        other_kustomization_yamls = ["./update-lb-ip/base"]
        all_kustomization_yamls = app_kustomization_yamls + other_kustomization_yamls
        for yaml in all_kustomization_yamls:
            kustomization_yaml = yaml + "/kustomization.yaml"
            parser = Parser(kustomization_yaml, "Kustomization")
            parser["namespace"] = self.settings["GLUU_NAMESPACE"]
            if yaml in app_kustomization_yamls:
                if "casa" in yaml:
                    update_image_name_tag("CASA_IMAGE_NAME", "CASA_IMAGE_TAG")
                elif "config" in yaml:
                    update_image_name_tag("CONFIG_IMAGE_NAME", "CONFIG_IMAGE_TAG")
                elif "cr-rotate" in yaml:
                    update_image_name_tag("CACHE_REFRESH_ROTATE_IMAGE_NAME", "CACHE_REFRESH_ROTATE_IMAGE_TAG")
                elif "key-rotation" in yaml:
                    update_image_name_tag("KEY_ROTATE_IMAGE_NAME", "KEY_ROTATE_IMAGE_TAG")
                elif "ldap" in yaml:
                    update_image_name_tag("LDAP_IMAGE_NAME", "LDAP_IMAGE_TAG")
                elif "jackrabbit" in yaml:
                    update_image_name_tag("JACKRABBIT_IMAGE_NAME", "JACKRABBIT_IMAGE_TAG")
                elif "oxauth" in yaml:
                    update_image_name_tag("OXAUTH_IMAGE_NAME", "OXAUTH_IMAGE_TAG")
                elif "oxd-server" in yaml:
                    update_image_name_tag("OXD_IMAGE_NAME", "OXD_IMAGE_TAG")
                elif "oxpassport" in yaml:
                    update_image_name_tag("OXPASSPORT_IMAGE_NAME", "OXPASSPORT_IMAGE_TAG")
                elif "oxshibboleth" in yaml:
                    update_image_name_tag("OXSHIBBOLETH_IMAGE_NAME", "OXSHIBBOLETH_IMAGE_TAG")
                elif "oxtrust" in yaml:
                    update_image_name_tag("OXTRUST_IMAGE_NAME", "OXTRUST_IMAGE_TAG")
                elif "persistence" in yaml:
                    update_image_name_tag("PERSISTENCE_IMAGE_NAME", "PERSISTENCE_IMAGE_TAG")
                elif "radius" in yaml:
                    update_image_name_tag("RADIUS_IMAGE_NAME", "RADIUS_IMAGE_TAG")
                elif "gluu-gateway-ui" in yaml:
                    update_image_name_tag("GLUU_GATEWAY_UI_IMAGE_NAME", "GLUU_GATEWAY_UI_IMAGE_TAG")
                    parser["namespace"] = self.settings["GG_UI_NAMESPACE"]
            parser.dump_it()

    def setup_tls(self):
        starting_time = time.time()
        while True:
            try:
                ssl_cert = self.kubernetes.read_namespaced_secret("gluu",
                                                                  self.settings["GLUU_NAMESPACE"]).data["ssl_cert"]
                ssl_key = self.kubernetes.read_namespaced_secret("gluu",
                                                                 self.settings["GLUU_NAMESPACE"]).data["ssl_key"]
                break
            except Exception:
                logger.info("Waiting for Gluu secret...")
                time.sleep(10)
                end_time = time.time()
                running_time = end_time - starting_time
                if running_time > 600:
                    logger.error("Could not read Gluu secret. Please check config job pod logs.")
                    raise SystemExit(1)

        self.kubernetes.patch_or_create_namespaced_secret(name="tls-certificate",
                                                          namespace=self.settings["GLUU_NAMESPACE"],
                                                          literal="tls.crt",
                                                          value_of_literal=ssl_cert,
                                                          secret_type="kubernetes.io/tls",
                                                          second_literal="tls.key",
                                                          value_of_second_literal=ssl_key)

    def register_op_client(self, client_name, op_host, oxd_url):
        logger.info("Registering a client for gg-ui named konga-client")

        add_curl = ["apk", "add", "curl"]
        data = '{"redirect_uris": ["https://' + op_host + '/gg-ui/"], "op_host": "' + op_host + \
               '", "post_logout_redirect_uris": ["https://' + op_host + \
               '/gg-ui/"], "scope": ["openid", "oxd", "permission", "username"], ' \
               '"grant_types": ["authorization_code", "client_credentials"], "client_name": "' + client_name + '"}'

        exec_curl_command = ["curl", "-k", "-s", "--location", "--request", "POST",
                             "{}/register-site".format(oxd_url), "--header",
                             "Content-Type: application/json", "--data-raw",
                             data]

        self.kubernetes.connect_get_namespaced_pod_exec(exec_command=add_curl,
                                                        app_label="app=oxtrust",
                                                        namespace=self.settings["GLUU_NAMESPACE"])
        client_registration_response = self.kubernetes.connect_get_namespaced_pod_exec(exec_command=exec_curl_command,
                                                                                       app_label="app=oxtrust",
                                                                                       namespace=self.settings["GLUU_NAMESPACE"])

        client_registration_response_dict = literal_eval(client_registration_response)
        oxd_id = client_registration_response_dict["oxd_id"]
        client_id = client_registration_response_dict["client_id"]
        client_secret = client_registration_response_dict["client_secret"]
        return oxd_id, client_id, client_secret

    def kustomize_config(self):
        config_kustmoization_yaml = Path("./config/base/kustomization.yaml")
        parser = Parser(config_kustmoization_yaml, "Kustomization")
        list_of_config_resource_files = parser["resources"]
        if self.settings["DEPLOYMENT_ARCH"] == "gke":
            if "cluster-role-bindings.yaml" not in list_of_config_resource_files:
                list_of_config_resource_files.append("cluster-role-bindings.yaml")
        else:
            if "cluster-role-bindings.yaml" in list_of_config_resource_files:
                list_of_config_resource_files.remove("cluster-role-bindings.yaml")
        parser["resources"] = list_of_config_resource_files
        # if gluu crt and key were provided by user
        custom_gluu_crt = Path("./gluu.crt")
        custom_gluu_key = Path("./gluu.key")
        if custom_gluu_crt.exists() and custom_gluu_key.exists():
            cert = open(custom_gluu_crt).read()
            key = open(custom_gluu_key).read()
            if not check_cert_with_private_key(cert, key):
                logger.error("Custom crt and key were provided but were incorrect")
                raise SystemExit(1)
            shutil.copy(custom_gluu_crt, Path("./config/base"))
            shutil.copy(custom_gluu_key, Path("./config/base"))
            parser.update({"secretGenerator": [{"name": "gluu-cert-key-override", "files": ["gluu.crt", "gluu.key"]}]})
            jobs_parser = Parser("./config/base/jobs.yaml", "Job")
            # Add volume mount
            jobs_parser["spec"]["template"]["spec"]["volumes"].append({"name": "gluu-cert-override", "secret": {
                "secretName": "gluu-cert-key-override", "items": [{"key": "gluu.crt", "path": "gluu_https.crt"}]}})
            jobs_parser["spec"]["template"]["spec"]["volumes"].append({"name": "gluu-key-override", "secret": {
                "secretName": "gluu-cert-key-override", "items": [{"key": "gluu.key", "path": "gluu_https.key"}]}})
            # Add volumeMounts
            jobs_parser["spec"]["template"]["spec"]["containers"][0]["volumeMounts"].append(
                {"name": "gluu-cert-override", "mountPath": "/etc/certs/gluu_https.crt", "subPath": "gluu_https.crt"})
            jobs_parser["spec"]["template"]["spec"]["containers"][0]["volumeMounts"].append(
                {"name": "gluu-key-override", "mountPath": "/etc/certs/gluu_https.key", "subPath": "gluu_https.key"})
            jobs_parser.dump_it()

        parser.dump_it()
        command = self.kubectl + " kustomize config/base > " + self.config_yaml
        subprocess_cmd(command)

        comfig_cm_parser = Parser(self.config_yaml, "ConfigMap", "config-cm")
        comfig_cm_parser["data"]["GLUU_CACHE_TYPE"] = self.settings["GLUU_CACHE_TYPE"]
        comfig_cm_parser["data"]["GLUU_CONFIG_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
        comfig_cm_parser["data"]["GLUU_SECRET_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
        comfig_cm_parser.dump_it()

    def kustomize_ldap(self):
        if self.settings["PERSISTENCE_BACKEND"] == "hybrid" or \
                self.settings["PERSISTENCE_BACKEND"] == "ldap":
            command = self.kubectl + " kustomize " + str(
                self.ldap_kustomize_yaml_directory.resolve()) + " > " + self.ldap_yaml
            subprocess_cmd(command)

            ldap_cm_parser = Parser(self.ldap_yaml, "ConfigMap")
            ldap_cm_parser["data"]["GLUU_CACHE_TYPE"] = self.settings["GLUU_CACHE_TYPE"]
            ldap_cm_parser["data"]["GLUU_PERSISTENCE_LDAP_MAPPING"] = self.settings["HYBRID_LDAP_HELD_DATA"]
            ldap_cm_parser["data"]["GLUU_PERSISTENCE_TYPE"] = self.settings["PERSISTENCE_BACKEND"]
            ldap_cm_parser["data"]["GLUU_CONFIG_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
            ldap_cm_parser["data"]["GLUU_SECRET_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
            ldap_cm_parser.dump_it()

            ldap_statefulset_parser = Parser(self.ldap_yaml, "StatefulSet")
            ldap_statefulset_parser["spec"]["volumeClaimTemplates"][0]["spec"]["resources"]["requests"]["storage"] \
                = self.settings["LDAP_STORAGE_SIZE"]
            # Remove resource limits on local installations
            if self.settings["DEPLOYMENT_ARCH"] == "microk8s" or self.settings["DEPLOYMENT_ARCH"] == "minikube":
                del ldap_statefulset_parser["spec"]["template"]["spec"]["containers"][0]["resources"]
            ldap_statefulset_parser.dump_it()

            if self.settings["APP_VOLUME_TYPE"] != 7 and self.settings["APP_VOLUME_TYPE"] != 12 and \
                    self.settings["APP_VOLUME_TYPE"] != 17:
                ldap_pv_parser = Parser(self.ldap_yaml, "PersistentVolume")
                ldap_pv_parser["spec"]["capacity"]["storage"] = self.settings["LDAP_STORAGE_SIZE"]
                if self.settings["APP_VOLUME_TYPE"] == 11:
                    ldap_pv_parser["spec"]["hostPath"]["path"] = self.settings["GOOGLE_NODE_HOME_DIR"] + "/opendj"
                ldap_pv_parser.dump_it()

    def kustomize_jackrabbit(self):
        command = self.kubectl + " kustomize " + str(
            self.jcr_kustomize_yaml_directory.resolve()) + " > " + self.jackrabbit_yaml
        subprocess_cmd(command)

        jackrabbit_cm_parser = Parser(self.jackrabbit_yaml, "ConfigMap")
        jackrabbit_cm_parser["data"]["GLUU_CONFIG_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
        jackrabbit_cm_parser["data"]["GLUU_SECRET_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
        jackrabbit_cm_parser.dump_it()

        jackrabbit_statefulset_parser = Parser(self.jackrabbit_yaml, "StatefulSet")
        jackrabbit_statefulset_parser["spec"]["volumeClaimTemplates"][0]["spec"]["resources"]["requests"]["storage"] \
            = self.settings["JACKRABBIT_STORAGE_SIZE"]
        # Remove resource limits on local installations
        if self.settings["DEPLOYMENT_ARCH"] == "microk8s" or self.settings["DEPLOYMENT_ARCH"] == "minikube":
            del jackrabbit_statefulset_parser["spec"]["template"]["spec"]["containers"][0]["resources"]
        jackrabbit_statefulset_parser.dump_it()

        if self.settings["APP_VOLUME_TYPE"] != 7 and self.settings["APP_VOLUME_TYPE"] != 12 and \
                self.settings["APP_VOLUME_TYPE"] != 17:
            jackrabbit_pv_parser = Parser(self.jackrabbit_yaml, "PersistentVolume")
            jackrabbit_pv_parser["spec"]["capacity"]["storage"] = self.settings["JACKRABBIT_STORAGE_SIZE"]
            if self.settings["APP_VOLUME_TYPE"] == 11:
                jackrabbit_pv_parser["spec"]["hostPath"]["path"] = self.settings["GOOGLE_NODE_HOME_DIR"] + "/jackrabbit"
            jackrabbit_pv_parser.dump_it()

    def kustomize_persistence(self):
        command = self.kubectl + " kustomize persistence/base > " + self.persistence_yaml
        subprocess_cmd(command)

        persistence_cm_parser = Parser(self.persistence_yaml, "ConfigMap")
        persistence_cm_parser["data"]["DOMAIN"] = self.settings["GLUU_FQDN"]
        persistence_cm_parser["data"]["GLUU_CACHE_TYPE"] = self.settings["GLUU_CACHE_TYPE"]
        if self.settings["GLUU_CACHE_TYPE"] == "REDIS":
            persistence_cm_parser["data"]["GLUU_REDIS_URL"] = self.settings["REDIS_URL"]
            persistence_cm_parser["data"]["GLUU_REDIS_TYPE"] = self.settings["REDIS_TYPE"]
            persistence_cm_parser["data"]["GLUU_REDIS_USE_SSL"] = self.settings["REDIS_USE_SSL"]
            persistence_cm_parser["data"]["GLUU_REDIS_SSL_TRUSTSTORE"] = self.settings["REDIS_SSL_TRUSTSTORE"]
            persistence_cm_parser["data"]["GLUU_REDIS_SENTINEL_GROUP"] = self.settings["REDIS_SENTINEL_GROUP"]
        persistence_cm_parser["data"]["GLUU_CASA_ENABLED"] = self.settings["ENABLE_CASA_BOOLEAN"]
        persistence_cm_parser["data"]["GLUU_COUCHBASE_URL"] = self.settings["COUCHBASE_URL"]
        persistence_cm_parser["data"]["GLUU_COUCHBASE_USER"] = self.settings["COUCHBASE_USER"]
        persistence_cm_parser["data"]["GLUU_OXTRUST_API_ENABLED"] = self.settings["ENABLE_OXTRUST_API_BOOLEAN"]
        persistence_cm_parser["data"]["GLUU_OXTRUST_API_TEST_MODE"] = self.settings["ENABLE_OXTRUST_TEST_MODE_BOOLEAN"]
        persistence_cm_parser["data"]["GLUU_PASSPORT_ENABLED"] = self.settings["ENABLE_OXPASSPORT_BOOLEAN"]
        persistence_cm_parser["data"]["GLUU_PERSISTENCE_LDAP_MAPPING"] = self.settings["HYBRID_LDAP_HELD_DATA"]
        persistence_cm_parser["data"]["GLUU_PERSISTENCE_TYPE"] = self.settings["PERSISTENCE_BACKEND"]
        persistence_cm_parser["data"]["GLUU_RADIUS_ENABLED"] = self.settings["ENABLE_RADIUS_BOOLEAN"]
        persistence_cm_parser["data"]["GLUU_SAML_ENABLED"] = self.settings["ENABLE_SAML_BOOLEAN"]
        persistence_cm_parser["data"]["GLUU_CONFIG_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
        persistence_cm_parser["data"]["GLUU_SECRET_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
        persistence_cm_parser["data"]["GLUU_JCA_RMI_URL"] = self.settings["JACKRABBIT_URL"] + "/rmi"
        persistence_cm_parser["data"]["GLUU_JCA_USERNAME"] = self.settings["JACKRABBIT_USER"]
        persistence_cm_parser.dump_it()

        if self.settings["PERSISTENCE_BACKEND"] == "ldap":
            persistence_job_parser = Parser(self.persistence_yaml, "Job")
            del persistence_job_parser["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]
            del persistence_job_parser["spec"]["template"]["spec"]["volumes"]
            persistence_job_parser.dump_it()

    def kustomize_oxauth(self):
        command = self.kubectl + " kustomize oxauth/base > " + self.oxauth_yaml
        subprocess_cmd(command)

        oxauth_cm_parser = Parser(self.oxauth_yaml, "ConfigMap")
        if self.settings["ENABLE_CASA_BOOLEAN"] == "true":
            oxauth_cm_parser["data"]["GLUU_SYNC_CASA_MANIFESTS"] = "true"
        oxauth_cm_parser["data"]["DOMAIN"] = self.settings["GLUU_FQDN"]
        oxauth_cm_parser["data"]["GLUU_CACHE_TYPE"] = self.settings["GLUU_CACHE_TYPE"]
        oxauth_cm_parser["data"]["GLUU_COUCHBASE_URL"] = self.settings["COUCHBASE_URL"]
        oxauth_cm_parser["data"]["GLUU_COUCHBASE_USER"] = self.settings["COUCHBASE_USER"]
        oxauth_cm_parser["data"]["GLUU_PERSISTENCE_LDAP_MAPPING"] = self.settings["HYBRID_LDAP_HELD_DATA"]
        oxauth_cm_parser["data"]["GLUU_PERSISTENCE_TYPE"] = self.settings["PERSISTENCE_BACKEND"]
        oxauth_cm_parser["data"]["GLUU_CONFIG_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
        oxauth_cm_parser["data"]["GLUU_SECRET_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
        oxauth_cm_parser["data"]["GLUU_JCA_URL"] = self.settings["JACKRABBIT_URL"]
        oxauth_cm_parser.dump_it()

        # Remove resource limits on local installations
        oxauth_deployment_parser = Parser(self.oxauth_yaml, "Deployment")
        if self.settings["DEPLOYMENT_ARCH"] == "microk8s" or self.settings["DEPLOYMENT_ARCH"] == "minikube":
            del oxauth_deployment_parser["spec"]["template"]["spec"]["containers"][0]["resources"]
        oxauth_deployment_parser.dump_it()

        self.adjust_yamls_for_fqdn_status[self.oxauth_yaml] = "Deployment"

    def kustomize_gluu_upgrade(self):
        command = self.kubectl + " kustomize upgrade/base > " + self.gluu_upgrade_yaml
        subprocess_cmd(command)
        upgrade_cm_parser = Parser(self.gluu_upgrade_yaml, "ConfigMap")
        upgrade_cm_parser["data"]["DOMAIN"] = self.settings["GLUU_FQDN"]
        upgrade_cm_parser["data"]["GLUU_CACHE_TYPE"] = self.settings["GLUU_CACHE_TYPE"]
        upgrade_cm_parser["data"]["GLUU_COUCHBASE_URL"] = self.settings["COUCHBASE_URL"]
        upgrade_cm_parser["data"]["GLUU_COUCHBASE_USER"] = self.settings["COUCHBASE_USER"]
        upgrade_cm_parser["data"]["GLUU_PERSISTENCE_LDAP_MAPPING"] = self.settings["HYBRID_LDAP_HELD_DATA"]
        upgrade_cm_parser["data"]["GLUU_PERSISTENCE_TYPE"] = self.settings["PERSISTENCE_BACKEND"]
        upgrade_cm_parser["data"]["GLUU_CONFIG_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
        upgrade_cm_parser["data"]["GLUU_SECRET_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
        upgrade_cm_parser.dump_it()

        upgrade_job_parser = Parser(self.gluu_upgrade_yaml, "Job")
        upgrade_job_parser["spec"]["template"]["spec"]["containers"][0]["args"] = \
            ["--source", self.settings["GLUU_VERSION"],
             "--target", self.settings["GLUU_UPGRADE_TARGET_VERSION"]]
        upgrade_job_parser.dump_it()

        self.adjust_yamls_for_fqdn_status[self.gluu_upgrade_yaml] = "Job"

    def kustomize_oxtrust(self):
        command = self.kubectl + " kustomize oxtrust/base > " + self.oxtrust_yaml
        subprocess_cmd(command)
        oxtrust_cm_parser = Parser(self.oxtrust_yaml, "ConfigMap")
        if self.settings["ENABLE_OXSHIBBOLETH"] != "Y":
            oxtrust_cm_parser["data"]["GLUU_SYNC_SHIB_MANIFESTS"] = "false"
        oxtrust_cm_parser["data"]["DOMAIN"] = self.settings["GLUU_FQDN"]
        oxtrust_cm_parser["data"]["GLUU_CACHE_TYPE"] = self.settings["GLUU_CACHE_TYPE"]
        oxtrust_cm_parser["data"]["GLUU_COUCHBASE_URL"] = self.settings["COUCHBASE_URL"]
        oxtrust_cm_parser["data"]["GLUU_COUCHBASE_USER"] = self.settings["COUCHBASE_USER"]
        oxtrust_cm_parser["data"]["GLUU_PERSISTENCE_LDAP_MAPPING"] = self.settings["HYBRID_LDAP_HELD_DATA"]
        oxtrust_cm_parser["data"]["GLUU_PERSISTENCE_TYPE"] = self.settings["PERSISTENCE_BACKEND"]
        oxtrust_cm_parser["data"]["GLUU_CONFIG_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
        oxtrust_cm_parser["data"]["GLUU_SECRET_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
        oxtrust_cm_parser["data"]["GLUU_JCA_URL"] = self.settings["JACKRABBIT_URL"]

        oxtrust_cm_parser.dump_it()

        self.adjust_yamls_for_fqdn_status[self.oxtrust_yaml] = "StatefulSet"

        # Remove resource limits on local installations
        oxtrust_statefulset_parser = Parser(self.oxtrust_yaml, "StatefulSet")
        if self.settings["DEPLOYMENT_ARCH"] == "microk8s" or self.settings["DEPLOYMENT_ARCH"] == "minikube":
            del oxtrust_statefulset_parser["spec"]["template"]["spec"]["containers"][0]["resources"]
        oxtrust_statefulset_parser.dump_it()

    def kustomize_oxshibboleth(self):
        if self.settings["ENABLE_OXSHIBBOLETH"] == "Y":
            command = self.kubectl + " kustomize oxshibboleth/base > " + self.oxshibboleth_yaml
            subprocess_cmd(command)

            oxshibboleth_cm_parser = Parser(self.oxshibboleth_yaml, "ConfigMap")
            oxshibboleth_cm_parser["data"]["DOMAIN"] = self.settings["GLUU_FQDN"]
            oxshibboleth_cm_parser["data"]["GLUU_COUCHBASE_URL"] = self.settings["COUCHBASE_URL"]
            oxshibboleth_cm_parser["data"]["GLUU_COUCHBASE_USER"] = self.settings["COUCHBASE_USER"]
            oxshibboleth_cm_parser["data"]["GLUU_PERSISTENCE_LDAP_MAPPING"] = self.settings["HYBRID_LDAP_HELD_DATA"]
            oxshibboleth_cm_parser["data"]["GLUU_PERSISTENCE_TYPE"] = self.settings["PERSISTENCE_BACKEND"]
            oxshibboleth_cm_parser["data"]["GLUU_CONFIG_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
            oxshibboleth_cm_parser["data"]["GLUU_SECRET_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
            oxshibboleth_cm_parser["data"]["GLUU_JCA_URL"] = self.settings["JACKRABBIT_URL"]
            oxshibboleth_cm_parser.dump_it()

            self.adjust_yamls_for_fqdn_status[self.oxshibboleth_yaml] = "StatefulSet"
            # Remove resource limits on local installations
            oxshibboleth_statefulset_parser = Parser(self.oxshibboleth_yaml, "StatefulSet")
            if self.settings["DEPLOYMENT_ARCH"] == "microk8s" or self.settings["DEPLOYMENT_ARCH"] == "minikube":
                del oxshibboleth_statefulset_parser["spec"]["template"]["spec"]["containers"][0]["resources"]
            oxshibboleth_statefulset_parser.dump_it()

    def kustomize_oxpassport(self):
        if self.settings["ENABLE_OXPASSPORT"] == "Y":
            command = self.kubectl + " kustomize oxpassport/base > " + self.oxpassport_yaml
            subprocess_cmd(command)

            oxpassport_cm_parser = Parser(self.oxpassport_yaml, "ConfigMap")
            oxpassport_cm_parser["data"]["DOMAIN"] = self.settings["GLUU_FQDN"]
            oxpassport_cm_parser["data"]["GLUU_COUCHBASE_URL"] = self.settings["COUCHBASE_URL"]
            oxpassport_cm_parser["data"]["GLUU_COUCHBASE_USER"] = self.settings["COUCHBASE_USER"]
            oxpassport_cm_parser["data"]["GLUU_PERSISTENCE_LDAP_MAPPING"] = self.settings["HYBRID_LDAP_HELD_DATA"]
            oxpassport_cm_parser["data"]["GLUU_PERSISTENCE_TYPE"] = self.settings["PERSISTENCE_BACKEND"]
            oxpassport_cm_parser["data"]["GLUU_CONFIG_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
            oxpassport_cm_parser["data"]["GLUU_SECRET_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
            oxpassport_cm_parser["data"]["GLUU_JCA_URL"] = self.settings["JACKRABBIT_URL"]

            oxpassport_cm_parser.dump_it()

            self.adjust_yamls_for_fqdn_status[self.oxpassport_yaml] = "Deployment"
            # Remove resource limits on local installations
            oxpassport_deployment_parser = Parser(self.oxpassport_yaml, "Deployment")
            if self.settings["DEPLOYMENT_ARCH"] == "microk8s" or self.settings["DEPLOYMENT_ARCH"] == "minikube":
                del oxpassport_deployment_parser["spec"]["template"]["spec"]["containers"][0]["resources"]
            oxpassport_deployment_parser.dump_it()

    def kustomize_key_rotation(self):
        if self.settings["ENABLE_KEY_ROTATE"] == "Y":
            command = self.kubectl + " kustomize key-rotation/base > " + self.key_rotate_yaml
            subprocess_cmd(command)

            key_rotate_cm_parser = Parser(self.key_rotate_yaml, "ConfigMap")
            key_rotate_cm_parser["data"]["GLUU_COUCHBASE_URL"] = self.settings["COUCHBASE_URL"]
            key_rotate_cm_parser["data"]["GLUU_COUCHBASE_USER"] = self.settings["COUCHBASE_USER"]
            key_rotate_cm_parser["data"]["GLUU_PERSISTENCE_LDAP_MAPPING"] = self.settings["HYBRID_LDAP_HELD_DATA"]
            key_rotate_cm_parser["data"]["GLUU_PERSISTENCE_TYPE"] = self.settings["PERSISTENCE_BACKEND"]
            key_rotate_cm_parser["data"]["GLUU_CONFIG_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
            key_rotate_cm_parser["data"]["GLUU_SECRET_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
            key_rotate_cm_parser.dump_it()

            self.adjust_yamls_for_fqdn_status[self.key_rotate_yaml] = "Deployment"
            # Remove resource limits on local installations
            key_rotation_deployment_parser = Parser(self.key_rotate_yaml, "Deployment")
            if self.settings["DEPLOYMENT_ARCH"] == "microk8s" or self.settings["DEPLOYMENT_ARCH"] == "minikube":
                del key_rotation_deployment_parser["spec"]["template"]["spec"]["containers"][0]["resources"]
            key_rotation_deployment_parser.dump_it()

    def kustomize_cr_rotate(self):
        if self.settings["ENABLE_CACHE_REFRESH"] == "Y":
            command = self.kubectl + " kustomize cr-rotate/base > " + self.cr_rotate_yaml
            subprocess_cmd(command)

            cr_rotate_cm_parser = Parser(self.cr_rotate_yaml, "ConfigMap")
            cr_rotate_cm_parser["data"]["GLUU_COUCHBASE_URL"] = self.settings["COUCHBASE_URL"]
            cr_rotate_cm_parser["data"]["GLUU_COUCHBASE_USER"] = self.settings["COUCHBASE_USER"]
            cr_rotate_cm_parser["data"]["GLUU_PERSISTENCE_LDAP_MAPPING"] = self.settings["HYBRID_LDAP_HELD_DATA"]
            cr_rotate_cm_parser["data"]["GLUU_PERSISTENCE_TYPE"] = self.settings["PERSISTENCE_BACKEND"]
            cr_rotate_cm_parser["data"]["GLUU_CONFIG_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
            cr_rotate_cm_parser["data"]["GLUU_SECRET_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
            cr_rotate_cm_parser.dump_it()

            self.adjust_yamls_for_fqdn_status[self.cr_rotate_yaml] = "DaemonSet"
            # Remove resource limits on local installations
            cr_rotate_daemonset_parser = Parser(self.cr_rotate_yaml, "DaemonSet")
            if self.settings["DEPLOYMENT_ARCH"] == "microk8s" or self.settings["DEPLOYMENT_ARCH"] == "minikube":
                del cr_rotate_daemonset_parser["spec"]["template"]["spec"]["containers"][0]["resources"]
            cr_rotate_daemonset_parser.dump_it()

    def kustomize_oxd_server(self):
        if self.settings["ENABLE_OXD"] == "Y":
            command = self.kubectl + " kustomize oxd-server/base > " + self.oxd_server_yaml
            subprocess_cmd(command)

            oxd_server_cm_parser = Parser(self.oxd_server_yaml, "ConfigMap")
            oxd_server_cm_parser["data"]["GLUU_COUCHBASE_URL"] = self.settings["COUCHBASE_URL"]
            oxd_server_cm_parser["data"]["GLUU_COUCHBASE_USER"] = self.settings["COUCHBASE_USER"]
            oxd_server_cm_parser["data"]["GLUU_PERSISTENCE_LDAP_MAPPING"] = self.settings["HYBRID_LDAP_HELD_DATA"]
            oxd_server_cm_parser["data"]["GLUU_PERSISTENCE_TYPE"] = self.settings["PERSISTENCE_BACKEND"]
            oxd_server_cm_parser["data"]["ADMIN_KEYSTORE_PASSWORD"] = self.settings["OXD_SERVER_PW"]
            oxd_server_cm_parser["data"]["APPLICATION_KEYSTORE_PASSWORD"] = self.settings["OXD_SERVER_PW"]
            oxd_server_cm_parser["data"]["APPLICATION_KEYSTORE_CN"] = self.settings["OXD_APPLICATION_KEYSTORE_CN"]
            oxd_server_cm_parser["data"]["ADMIN_KEYSTORE_CN"] = self.settings["OXD_ADMIN_KEYSTORE_CN"]
            oxd_server_cm_parser["data"]["GLUU_SERVER_HOST"] = self.settings["GLUU_FQDN"]
            oxd_server_cm_parser["data"]["GLUU_CONFIG_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
            oxd_server_cm_parser["data"]["GLUU_SECRET_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
            oxd_server_cm_parser.dump_it()

            oxd_server_service_parser = Parser(self.oxd_server_yaml, "Service")
            oxd_server_service_parser["metadata"]["name"] = self.settings["OXD_APPLICATION_KEYSTORE_CN"]
            oxd_server_service_parser.dump_it()
            # Remove resource limits on local installations
            oxd_server_deployment_parser = Parser(self.oxd_server_yaml, "Deployment")
            if self.settings["DEPLOYMENT_ARCH"] == "microk8s" or self.settings["DEPLOYMENT_ARCH"] == "minikube":
                del oxd_server_deployment_parser["spec"]["template"]["spec"]["containers"][0]["resources"]
            oxd_server_deployment_parser.dump_it()

            self.adjust_yamls_for_fqdn_status[self.oxd_server_yaml] = "Deployment"

    def kustomize_casa(self):
        if self.settings["ENABLE_CASA"] == "Y":
            command = self.kubectl + " kustomize casa/base > " + self.casa_yaml
            subprocess_cmd(command)

            casa_cm_parser = Parser(self.casa_yaml, "ConfigMap")
            casa_cm_parser["data"]["DOMAIN"] = self.settings["GLUU_FQDN"]
            casa_cm_parser["data"]["GLUU_CACHE_TYPE"] = self.settings["GLUU_CACHE_TYPE"]
            casa_cm_parser["data"]["GLUU_COUCHBASE_URL"] = self.settings["COUCHBASE_URL"]
            casa_cm_parser["data"]["GLUU_COUCHBASE_USER"] = self.settings["COUCHBASE_USER"]
            casa_cm_parser["data"]["GLUU_PERSISTENCE_LDAP_MAPPING"] = self.settings["HYBRID_LDAP_HELD_DATA"]
            casa_cm_parser["data"]["GLUU_PERSISTENCE_TYPE"] = self.settings["PERSISTENCE_BACKEND"]
            casa_cm_parser["data"]["GLUU_CONFIG_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
            casa_cm_parser["data"]["GLUU_SECRET_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
            casa_cm_parser["data"]["GLUU_OXD_SERVER_URL"] = self.settings["OXD_APPLICATION_KEYSTORE_CN"] + ":8443"
            casa_cm_parser["data"]["GLUU_JCA_URL"] = self.settings["JACKRABBIT_URL"]
            casa_cm_parser.dump_it()

            self.adjust_yamls_for_fqdn_status[self.casa_yaml] = "Deployment"
            # Remove resource limits on local installations
            casa_server_deployment_parser = Parser(self.casa_yaml, "Deployment")
            if self.settings["DEPLOYMENT_ARCH"] == "microk8s" or self.settings["DEPLOYMENT_ARCH"] == "minikube":
                del casa_server_deployment_parser["spec"]["template"]["spec"]["containers"][0]["resources"]
            casa_server_deployment_parser.dump_it()

    def kustomize_radius(self):
        if self.settings["ENABLE_RADIUS"] == "Y":
            command = self.kubectl + " kustomize radius/base > " + self.radius_yaml
            subprocess_cmd(command)

            radius_cm_parser = Parser(self.radius_yaml, "ConfigMap")
            radius_cm_parser["data"]["DOMAIN"] = self.settings["GLUU_FQDN"]
            radius_cm_parser["data"]["GLUU_CACHE_TYPE"] = self.settings["GLUU_CACHE_TYPE"]
            radius_cm_parser["data"]["GLUU_COUCHBASE_URL"] = self.settings["COUCHBASE_URL"]
            radius_cm_parser["data"]["GLUU_COUCHBASE_USER"] = self.settings["COUCHBASE_USER"]
            radius_cm_parser["data"]["GLUU_PERSISTENCE_LDAP_MAPPING"] = self.settings["HYBRID_LDAP_HELD_DATA"]
            radius_cm_parser["data"]["GLUU_PERSISTENCE_TYPE"] = self.settings["PERSISTENCE_BACKEND"]
            radius_cm_parser["data"]["GLUU_CONFIG_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
            radius_cm_parser["data"]["GLUU_SECRET_KUBERNETES_NAMESPACE"] = self.settings["GLUU_NAMESPACE"]
            radius_cm_parser.dump_it()

            self.adjust_yamls_for_fqdn_status[self.radius_yaml] = "Deployment"
            # Remove resource limits on local installations
            radius_server_deployment_parser = Parser(self.radius_yaml, "Deployment")
            if self.settings["DEPLOYMENT_ARCH"] == "microk8s" or self.settings["DEPLOYMENT_ARCH"] == "minikube":
                del radius_server_deployment_parser["spec"]["template"]["spec"]["containers"][0]["resources"]
            radius_server_deployment_parser.dump_it()

    def kustomize_update_lb_ip(self):
        if self.settings["IS_GLUU_FQDN_REGISTERED"] != "Y":
            if self.settings["DEPLOYMENT_ARCH"] == "eks":
                command = self.kubectl + " kustomize update-lb-ip/base > " + self.update_lb_ip_yaml
                subprocess_cmd(command)

    def kustomize_gg_ui(self):
        if self.settings["INSTALL_GLUU_GATEWAY"] == "Y":
            command = self.kubectl + " kustomize gluu-gateway-ui/base > " + self.gg_ui_yaml
            subprocess_cmd(command)
            oxd_server_url = "https://{}.{}.svc.cluster.local:8443".format(
                self.settings["OXD_APPLICATION_KEYSTORE_CN"], self.settings["GLUU_NAMESPACE"])
            gg_ui_cm_parser = Parser(self.gg_ui_yaml, "ConfigMap")
            gg_ui_cm_parser["data"]["DB_USER"] = self.settings["GG_UI_PG_USER"]
            gg_ui_cm_parser["data"]["KONG_ADMIN_URL"] = "https://kong-admin.{}.svc.cluster.local:8444".format(
                self.settings["KONG_NAMESPACE"])
            gg_ui_cm_parser["data"]["DB_HOST"] = self.settings["POSTGRES_URL"]
            gg_ui_cm_parser["data"]["DB_DATABASE"] = self.settings["GG_UI_DATABASE"]
            gg_ui_cm_parser["data"]["OXD_SERVER_URL"] = oxd_server_url
            # Register new client if one was not provided
            if not gg_ui_cm_parser["data"]["CLIENT_ID"] or \
                    not gg_ui_cm_parser["data"]["OXD_ID"] or \
                    not gg_ui_cm_parser["data"]["CLIENT_SECRET"]:
                oxd_id, client_id, client_secret = self.register_op_client("konga-client",
                                                                           self.settings["GLUU_FQDN"],
                                                                           oxd_server_url)
                gg_ui_cm_parser["data"]["OXD_ID"] = oxd_id
                gg_ui_cm_parser["data"]["CLIENT_ID"] = client_id
                gg_ui_cm_parser["data"]["CLIENT_SECRET"] = client_secret
            gg_ui_cm_parser["data"]["OP_SERVER_URL"] = self.settings["GLUU_FQDN"]

            gg_ui_cm_parser["data"]["GG_HOST"] = self.settings["GLUU_FQDN"] + "/gg-ui/"
            gg_ui_cm_parser["data"]["GG_UI_REDIRECT_URL_HOST"] = self.settings["GLUU_FQDN"] + "/gg-ui/"

            gg_ui_cm_parser.dump_it()

            postgres_full_add = "'postgresql://" + self.settings["GG_UI_PG_USER"] + ":" + \
                                self.settings["GG_UI_PG_PASSWORD"] + "@" + self.settings["POSTGRES_URL"] + \
                                ":5432/" + self.settings["GG_UI_DATABASE"] + "'"
            gg_ui_job_parser = Parser(self.gg_ui_yaml, "Job")
            gg_ui_job_parser["spec"]["template"]["spec"]["containers"][0]["command"] = \
                ["/bin/sh", "-c", "./start.sh -c prepare -a postgres -u " + postgres_full_add]
            gg_ui_job_parser.dump_it()

            gg_ui_ingress_parser = Parser(self.gg_ui_yaml, "Ingress")
            gg_ui_ingress_parser["spec"]["tls"][0]["hosts"][0] = self.settings["GLUU_FQDN"]
            gg_ui_ingress_parser["spec"]["rules"][0]["host"] = self.settings["GLUU_FQDN"]
            gg_ui_ingress_parser.dump_it()

            # Remove resource limits on local installations
            gg_ui_deployment_parser = Parser(self.gg_ui_yaml, "Deployment")
            if self.settings["DEPLOYMENT_ARCH"] == "microk8s" or self.settings["DEPLOYMENT_ARCH"] == "minikube":
                try:
                    del gg_ui_deployment_parser["spec"]["template"]["spec"]["containers"][0]["resources"]
                except KeyError:
                    logger.info("Resources not deleted as they are not found inside yaml.")
            gg_ui_deployment_parser.dump_it()

            self.adjust_yamls_for_fqdn_status[self.gg_ui_yaml] = "Deployment"

    def deploy_alb(self):
        shutil.copy(Path("./alb/ingress.yaml"), self.output_yaml_directory.joinpath("ingress.yaml"))
        self.kubernetes.create_objects_from_dict(self.output_yaml_directory.joinpath("ingress.yaml"),
                                                 self.settings["GLUU_NAMESPACE"])
        if self.settings["IS_GLUU_FQDN_REGISTERED"] != "Y":
            prompt = input("Please input the DNS of the Application load balancer  created found on AWS UI: ")
            lb_hostname = prompt
            while True:
                try:
                    if lb_hostname:
                        break
                    lb_hostname = self.kubernetes.read_namespaced_ingress(
                        name="gluu", namespace="gluu").status.load_balancer.ingress[0].hostname
                except TypeError:
                    logger.info("Waiting for loadbalancer address..")
                    time.sleep(10)
            self.settings["LB_ADD"] = lb_hostname

    def check_lb(self):
        lb_hostname = None
        while True:
            try:
                if lb_hostname:
                    break
                lb_hostname = self.kubernetes.read_namespaced_service(
                    name="ingress-nginx", namespace="ingress-nginx").status.load_balancer.ingress[0].hostname
            except TypeError:
                logger.info("Waiting for loadbalancer address..")
                time.sleep(10)
        self.settings["LB_ADD"] = lb_hostname

    def deploy_nginx(self):
        copy(Path("./nginx"), self.output_yaml_directory.joinpath("nginx"))
        if self.settings["DEPLOYMENT_ARCH"] == "minikube":
            subprocess_cmd("minikube addons enable ingress")
        self.kubernetes.create_objects_from_dict(self.output_yaml_directory.joinpath("nginx/mandatory.yaml"))
        if self.settings["DEPLOYMENT_ARCH"] == "eks":
            lb_hostname = None
            if self.settings["AWS_LB_TYPE"] == "nlb":
                if self.settings["USE_ARN"] == "Y":
                    svc_nlb_yaml = self.output_yaml_directory.joinpath("nginx/nlb-service.yaml")
                    svc_nlb_yaml_parser = Parser(svc_nlb_yaml, "Service")
                    svc_nlb_yaml_parser["metadata"]["annotations"].update(
                        {"service.beta.kubernetes.io/aws-load-balancer-ssl-cert": self.settings["ARN_AWS_IAM"]})
                    svc_nlb_yaml_parser["metadata"]["annotations"].update(
                        {"service.beta.kubernetes.io/aws-load-balancer-cross-zone-load-balancing-enabled": '"true"'})
                    svc_nlb_yaml_parser["metadata"]["annotations"].update({
                        "service.beta.kubernetes.io/aws-load-balancer-ssl-negotiation-policy": "ELBSecurityPolicy-TLS-1-1-2017-01"})
                    svc_nlb_yaml_parser["metadata"]["annotations"].update(
                        {"service.beta.kubernetes.io/aws-load-balancer-backend-protocol": "http"})
                    svc_nlb_yaml_parser["metadata"]["annotations"].update(
                        {"service.beta.kubernetes.io/aws-load-balancer-ssl-ports": "https"})
                    svc_nlb_yaml_parser.dump_it()
                self.kubernetes.create_objects_from_dict(self.output_yaml_directory.joinpath("nginx/nlb-service.yaml"))
                while True:
                    try:
                        ip_static = None
                        lb_hostname = self.kubernetes.read_namespaced_service(
                            name="ingress-nginx", namespace="ingress-nginx").status.load_balancer.ingress[0].hostname
                        try:
                            ip_static = socket.gethostbyname(str(lb_hostname))
                        except socket.gaierror:
                            logger.warning("IP not assigned yet")
                        if ip_static:
                            break
                    except TypeError:
                        logger.info("Waiting for LB to receive an ip assignment from AWS")
                    time.sleep(10)
            else:
                if self.settings["USE_ARN"] == "Y":
                    svc_l7_yaml = self.output_yaml_directory.joinpath("nginx/service-l7.yaml")
                    svc_l7_yaml_parser = Parser(svc_l7_yaml, "Service")
                    svc_l7_yaml_parser["metadata"]["annotations"][
                        "service.beta.kubernetes.io/aws-load-balancer-ssl-cert"] = self.settings["ARN_AWS_IAM"]
                    svc_l7_yaml_parser.dump_it()
                    self.kubernetes.create_objects_from_dict(svc_l7_yaml)
                    self.kubernetes.delete_config_map_using_name("nginx-configuration", "ingress-nginx")
                    time.sleep(5)
                    self.kubernetes.create_objects_from_dict(self.output_yaml_directory.
                                                             joinpath("nginx/patch-configmap-l7.yaml"))
                else:
                    self.kubernetes.delete_config_map_using_name("nginx-configuration", "ingress-nginx")
                    time.sleep(5)
                    self.kubernetes.create_objects_from_dict(self.output_yaml_directory.
                                                             joinpath("nginx/service-l4.yaml"))
                    self.kubernetes.create_objects_from_dict(self.output_yaml_directory.
                                                             joinpath("nginx/patch-configmap-l4.yaml"))

            self.check_lb()

        if self.settings["DEPLOYMENT_ARCH"] == "gke" or self.settings["DEPLOYMENT_ARCH"] == "aks":
            self.kubernetes.create_objects_from_dict(self.output_yaml_directory.joinpath("nginx/cloud-generic.yaml"))
            ip = None
            while True:
                try:
                    if ip:
                        break
                    ip = self.kubernetes.read_namespaced_service(
                        name="ingress-nginx", namespace="ingress-nginx").status.load_balancer.ingress[0].ip
                except TypeError:
                    logger.info("Waiting for the ip of the Loadbalancer")
                    time.sleep(10)
            logger.info(ip)
            self.settings["HOST_EXT_IP"] = ip

        ingress_name_list = ["gluu-ingress-base", "gluu-ingress-openid-configuration",
                             "gluu-ingress-uma2-configuration", "gluu-ingress-webfinger",
                             "gluu-ingress-simple-web-discovery", "gluu-ingress-scim-configuration",
                             "gluu-ingress-fido-u2f-configuration", "gluu-ingress", "gluu-ingress-stateful",
                             "gluu-casa", "gluu-ingress-fido2-configuration"]

        for ingress_name in ingress_name_list:
            yaml = self.output_yaml_directory.joinpath("nginx/nginx.yaml")
            parser = Parser(yaml, "Ingress", ingress_name)
            parser["spec"]["tls"][0]["hosts"][0] = self.settings["GLUU_FQDN"]
            parser["spec"]["rules"][0]["host"] = self.settings["GLUU_FQDN"]
            parser.dump_it()

        self.kubernetes.create_objects_from_dict(self.output_yaml_directory.joinpath("nginx/nginx.yaml"),
                                                 self.settings["GLUU_NAMESPACE"])

    def deploy_kubedb(self, helm=False):
        self.uninstall_kubedb()
        self.kubernetes.create_namespace(name="gluu-kubedb")
        if self.settings["DEPLOYMENT_ARCH"] == "gke":
            exec_cmd("kubectl create clusterrolebinding 'cluster-admin-$(whoami)' "
                     "--clusterrole=cluster-admin --user='$(gcloud config get-value core/account)'")

        if self.settings["DEPLOYMENT_ARCH"] == "microk8s" or helm:
            try:
                exec_cmd("helm repo add appscode https://charts.appscode.com/stable/")
                exec_cmd("helm repo update")
                exec_cmd("helm install kubedb-operator appscode/kubedb  --version v0.13.0-rc.0 "
                         "--namespace gluu-kubedb")
                self.kubernetes.check_pods_statuses("gluu-kubedb", "app=kubedb", self.timeout)
                exec_cmd("helm install kubedb-catalog appscode/kubedb-catalog  --version v0.13.0-rc.0 "
                         "--namespace gluu-kubedb")
            except FileNotFoundError:
                logger.error("Helm v3 is not installed. Please install it to continue "
                             "https://helm.sh/docs/intro/install/")
                raise SystemExit(1)
        else:
            exec_cmd("bash ./redis/kubedb.sh --namespace=gluu-kubedb --install-catalog=catalog.kubedb.com/v1alpha1")

    def deploy_postgres(self):
        self.uninstall_postgres()
        self.kubernetes.create_namespace(name=self.settings["POSTGRES_NAMESPACE"])
        postgres_init_sql = "CREATE USER {};\nALTER USER {} PASSWORD '{}';\nCREATE USER {};\n" \
                            "ALTER USER {} PASSWORD '{}';\nCREATE DATABASE {};\n" \
                            "GRANT ALL PRIVILEGES ON DATABASE {} TO {};\nCREATE DATABASE {};\n" \
                            "GRANT ALL PRIVILEGES ON DATABASE {} TO {};".format(self.settings["KONG_PG_USER"],
                                                                                self.settings["KONG_PG_USER"],
                                                                                self.settings["KONG_PG_PASSWORD"],
                                                                                self.settings["GG_UI_PG_USER"],
                                                                                self.settings["GG_UI_PG_USER"],
                                                                                self.settings["GG_UI_PG_PASSWORD"],
                                                                                self.settings["KONG_DATABASE"],
                                                                                self.settings["KONG_DATABASE"],
                                                                                self.settings["KONG_PG_USER"],
                                                                                self.settings["GG_UI_DATABASE"],
                                                                                self.settings["GG_UI_DATABASE"],
                                                                                self.settings["GG_UI_PG_USER"]
                                                                                )
        encoded_postgers_init_bytes = base64.b64encode(postgres_init_sql.encode("utf-8"))
        encoded_postgers_init_string = str(encoded_postgers_init_bytes, "utf-8")
        self.kubernetes.patch_or_create_namespaced_secret(name="pg-init-sql",
                                                          namespace=self.settings["POSTGRES_NAMESPACE"],
                                                          literal="data.sql",
                                                          value_of_literal=encoded_postgers_init_string)
        postgres_storage_class = Path("./postgres/storageclasses.yaml")
        self.analyze_storage_class(postgres_storage_class)
        self.kubernetes.create_objects_from_dict(postgres_storage_class)

        postgres_yaml = Path("./postgres/postgres.yaml")
        postgres_parser = Parser(postgres_yaml, "Postgres")
        postgres_parser["spec"]["replicas"] = self.settings["POSTGRES_REPLICAS"]
        postgres_parser["spec"]["monitor"]["prometheus"]["namespace"] = self.settings["POSTGRES_NAMESPACE"]
        postgres_parser["metadata"]["namespace"] = self.settings["POSTGRES_NAMESPACE"]
        if self.settings["DEPLOYMENT_ARCH"] == "microk8s" or self.settings["DEPLOYMENT_ARCH"] == "minikube":
            try:
                del postgres_parser["spec"]["podTemplate"]["spec"]["resources"]
            except KeyError:
                logger.info("Resources not deleted as they are not found inside yaml.")

        postgres_parser.dump_it()
        try:
            exec_cmd("kubectl apply -f {}".format(postgres_yaml))
        except:
            exec_cmd("microk8s.kubectl apply -f {}".format(postgres_yaml))

        if not self.settings["AWS_LB_TYPE"] == "alb":
            self.kubernetes.check_pods_statuses(self.settings["GLUU_NAMESPACE"], "app=postgres", self.timeout)

    def deploy_kong(self):
        self.uninstall_kong()
        self.kubernetes.create_namespace(name=self.settings["KONG_NAMESPACE"])
        encoded_kong_pass_bytes = base64.b64encode(self.settings["KONG_PG_PASSWORD"].encode("utf-8"))
        encoded_kong_pass_string = str(encoded_kong_pass_bytes, "utf-8")
        self.kubernetes.patch_or_create_namespaced_secret(name="kong-postgres-pass",
                                                          namespace=self.settings["KONG_NAMESPACE"],
                                                          literal="KONG_PG_PASSWORD",
                                                          value_of_literal=encoded_kong_pass_string)
        kong_init_job = Path("./gluu-gateway-ui/kong-init-job.yaml")
        kong_init_job_parser = Parser(kong_init_job, "Job")
        kong_init_job_parser["spec"]["template"]["spec"]["containers"][0]["env"] = [
            {"name": "KONG_DATABASE", "value": "postgres"},
            {"name": "KONG_PG_HOST", "value": self.settings["POSTGRES_URL"]},
            {"name": "KONG_PG_USER", "value": self.settings["KONG_PG_USER"]},
            {"name": "KONG_PG_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "kong-postgres-pass",
                                                                        "key": "KONG_PG_PASSWORD"}}}
        ]
        kong_init_job_parser["metadata"]["namespace"] = self.settings["KONG_NAMESPACE"]
        kong_init_job_parser.dump_it()
        self.kubernetes.create_objects_from_dict(kong_init_job)
        kong_all_in_one_db = Path("./gluu-gateway-ui/kong-all-in-one-db.yaml")

        kong_all_in_one_db_parser_sa = Parser(kong_all_in_one_db, "ServiceAccount")
        kong_all_in_one_db_parser_sa["metadata"]["namespace"] = self.settings["KONG_NAMESPACE"]
        kong_all_in_one_db_parser_sa.dump_it()

        kong_all_in_one_db_parser_crb = Parser(kong_all_in_one_db, "ClusterRoleBinding")
        kong_all_in_one_db_parser_crb["subjects"][0]["namespace"] = self.settings["KONG_NAMESPACE"]
        kong_all_in_one_db_parser_crb.dump_it()

        kong_all_in_one_db_parser_cm = Parser(kong_all_in_one_db, "ConfigMap")
        kong_all_in_one_db_parser_cm["metadata"]["namespace"] = self.settings["KONG_NAMESPACE"]
        kong_all_in_one_db_parser_cm.dump_it()

        kong_all_in_one_db_parser_svc_proxy = Parser(kong_all_in_one_db, "Service", "kong-proxy")
        kong_all_in_one_db_parser_svc_proxy["metadata"]["namespace"] = self.settings["KONG_NAMESPACE"]
        kong_all_in_one_db_parser_svc_proxy.dump_it()

        kong_all_in_one_db_parser_svc_webhook = Parser(kong_all_in_one_db, "Service", "kong-validation-webhook")
        kong_all_in_one_db_parser_svc_webhook["metadata"]["namespace"] = self.settings["KONG_NAMESPACE"]
        kong_all_in_one_db_parser_svc_webhook.dump_it()

        kong_all_in_one_db_parser_svc_admin = Parser(kong_all_in_one_db, "Service", "kong-admin")
        kong_all_in_one_db_parser_svc_admin["metadata"]["namespace"] = self.settings["KONG_NAMESPACE"]
        kong_all_in_one_db_parser_svc_admin.dump_it()

        kong_all_in_one_db_parser_deploy = Parser(kong_all_in_one_db, "Deployment")
        kong_containers = kong_all_in_one_db_parser_deploy["spec"]["template"]["spec"]["containers"]
        kong_all_in_one_db_parser_deploy["metadata"]["namespace"] = self.settings["KONG_NAMESPACE"]
        proxy_index = 0
        ingress_controller_index = 1
        for container in kong_containers:
            if container["name"] == "proxy":
                proxy_index = kong_containers.index(container)
            if container["name"] == "ingress-controller":
                ingress_controller_index = kong_containers.index(container)
        # Adjust proxy container envs
        env_list = kong_all_in_one_db_parser_deploy["spec"]["template"]["spec"]["containers"][proxy_index]["env"]
        for env in env_list:
            if env["name"] == "KONG_PG_HOST":
                env_list.remove(env)
            if env["name"] == "KONG_PG_USER":
                env_list.remove(env)
        env_list.append({"name": "KONG_PG_HOST", "value": self.settings["POSTGRES_URL"]})
        env_list.append({"name": "KONG_PG_USER", "value": self.settings["KONG_PG_USER"]})
        # Adjust kong ingress controller envs
        env_list = kong_all_in_one_db_parser_deploy["spec"]["template"]["spec"]["containers"][ingress_controller_index]["env"]
        for env in env_list:
            if env["name"] == "CONTROLLER_PUBLISH_SERVICE":
                env_list.remove(env)
        env_list.append({"name": "CONTROLLER_PUBLISH_SERVICE", "value":
                        self.settings["KONG_NAMESPACE"] + "/kong-proxy"})
        kong_all_in_one_db_parser_deploy["spec"]["template"]["spec"]["containers"][ingress_controller_index]["env"] \
            = env_list
        for container in kong_containers:
            if container["name"] == "proxy":
                container["image"] = self.settings["GLUU_GATEWAY_IMAGE_NAME"] + ":" + \
                                     self.settings["GLUU_GATEWAY_IMAGE_TAG"]
        kong_all_in_one_db_parser_deploy.dump_it()
        self.kubernetes.create_objects_from_dict(kong_all_in_one_db)
        if not self.settings["AWS_LB_TYPE"] == "alb":
            self.kubernetes.check_pods_statuses(self.settings["KONG_NAMESPACE"], "app=ingress-kong", self.timeout)

    def deploy_gg_ui(self):
        self.kubernetes.create_namespace(name=self.settings["GG_UI_NAMESPACE"])
        try:
            # Try to get gluu cert + key
            ssl_cert = self.kubernetes.read_namespaced_secret("gluu",
                                                              self.settings["GLUU_NAMESPACE"]).data["ssl_cert"]
            ssl_key = self.kubernetes.read_namespaced_secret("gluu",
                                                             self.settings["GLUU_NAMESPACE"]).data["ssl_key"]

            self.kubernetes.patch_or_create_namespaced_secret(name="tls-certificate",
                                                              namespace=self.settings["GG_UI_NAMESPACE"],
                                                              literal="tls.crt",
                                                              value_of_literal=ssl_cert,
                                                              secret_type="kubernetes.io/tls",
                                                              second_literal="tls.key",
                                                              value_of_second_literal=ssl_key)

        except Exception:
            logger.error("Could not read Gluu secret. Please check config job pod logs. GG-UI will deploy but fail. "
                         "Please mount crt and key inside gg-ui deployment")

        encoded_gg_ui_pg_pass_bytes = base64.b64encode(self.settings["GG_UI_PG_PASSWORD"].encode("utf-8"))
        encoded_gg_ui_pg_pass_string = str(encoded_gg_ui_pg_pass_bytes, "utf-8")

        self.kubernetes.patch_or_create_namespaced_secret(name="gg-ui-postgres-pass",
                                                          namespace=self.settings["GG_UI_NAMESPACE"],
                                                          literal="DB_PASSWORD",
                                                          value_of_literal=encoded_gg_ui_pg_pass_string)

        self.kubernetes.create_objects_from_dict(self.gg_ui_yaml)
        if not self.settings["AWS_LB_TYPE"] == "alb":
            self.kubernetes.check_pods_statuses(self.settings["GG_UI_NAMESPACE"], "app=gg-kong-ui", self.timeout)

    def install_gluugateway_dbmode(self):
        self.kustomize_gg_ui()
        self.adjust_fqdn_yaml_entries()
        self.deploy_postgres()
        self.deploy_kong()
        self.deploy_gg_ui()

    def deploy_redis(self):
        self.uninstall_redis()
        self.kubernetes.create_namespace(name=self.settings["REDIS_NAMESPACE"])
        redis_storage_class = Path("./redis/storageclasses.yaml")
        self.analyze_storage_class(redis_storage_class)
        self.kubernetes.create_objects_from_dict(redis_storage_class)

        redis_configmap = Path("./redis/configmaps.yaml")
        redis_conf_parser = Parser(redis_configmap, "ConfigMap")
        redis_conf_parser["metadata"]["namespace"] = self.settings["REDIS_NAMESPACE"]
        redis_conf_parser.dump_it()
        self.kubernetes.create_objects_from_dict(redis_configmap)

        redis_yaml = Path("./redis/redis.yaml")
        redis_parser = Parser(redis_yaml, "Redis")
        redis_parser["spec"]["cluster"]["master"] = self.settings["REDIS_MASTER_NODES"]
        redis_parser["spec"]["cluster"]["replicas"] = self.settings["REDIS_NODES_PER_MASTER"]
        redis_parser["spec"]["monitor"]["prometheus"]["namespace"] = self.settings["REDIS_NAMESPACE"]
        redis_parser["metadata"]["namespace"] = self.settings["REDIS_NAMESPACE"]
        if self.settings["DEPLOYMENT_ARCH"] == "microk8s" or self.settings["DEPLOYMENT_ARCH"] == "minikube":
            del redis_parser["spec"]["podTemplate"]["spec"]["resources"]
        redis_parser.dump_it()
        try:
            exec_cmd("kubectl apply -f {}".format(redis_yaml))
        except:
            exec_cmd("microk8s.kubectl apply -f {}".format(redis_yaml))

        if not self.settings["AWS_LB_TYPE"] == "alb":
            self.kubernetes.check_pods_statuses(self.settings["GLUU_NAMESPACE"], "app=redis-cluster", self.timeout)

    def deploy_config(self):
        self.kubernetes.create_objects_from_dict(self.config_yaml)
        if not self.settings["AWS_LB_TYPE"] == "alb":
            self.kubernetes.check_pods_statuses(self.settings["GLUU_NAMESPACE"], "app=config-init-load", self.timeout)

    def deploy_ldap(self):
        self.kubernetes.create_objects_from_dict(self.ldap_yaml)
        logger.info("Deploying LDAP.Please wait..")
        time.sleep(10)
        if not self.settings["AWS_LB_TYPE"] == "alb":
            self.kubernetes.check_pods_statuses(self.settings["GLUU_NAMESPACE"], "app=opendj", self.timeout)

    def deploy_jackrabbit(self):
        self.kubernetes.create_objects_from_dict(self.jackrabbit_yaml)
        logger.info("Deploying Jackrabbit content repository.Please wait..")
        time.sleep(10)
        if not self.settings["AWS_LB_TYPE"] == "alb":
            self.kubernetes.check_pods_statuses(self.settings["GLUU_NAMESPACE"], "app=jackrabbit", self.timeout)

    def deploy_persistence(self):
        self.kubernetes.create_objects_from_dict(self.persistence_yaml)
        logger.info("Trying to import ldifs...")
        if not self.settings["AWS_LB_TYPE"] == "alb":
            self.kubernetes.check_pods_statuses(self.settings["GLUU_NAMESPACE"], "app=persistence-load", self.timeout)
        if self.settings["PERSISTENCE_BACKEND"] == "hybrid" or \
                self.settings["PERSISTENCE_BACKEND"] == "ldap":
            self.kubernetes.patch_namespaced_stateful_set_scale(name="opendj",
                                                                replicas=self.settings["LDAP_REPLICAS"],
                                                                namespace=self.settings["GLUU_NAMESPACE"])
            if not self.settings["AWS_LB_TYPE"] == "alb":
                self.kubernetes.check_pods_statuses(self.settings["GLUU_NAMESPACE"], "app=opendj", self.timeout)

    def deploy_update_lb_ip(self):
        self.kubernetes.create_objects_from_dict(self.update_lb_ip_yaml)

    def deploy_oxauth(self):
        self.kubernetes.create_objects_from_dict(self.oxauth_yaml)
        if not self.settings["AWS_LB_TYPE"] == "alb":
            self.kubernetes.check_pods_statuses(self.settings["GLUU_NAMESPACE"], "app=oxauth", self.timeout)
        self.kubernetes.patch_namespaced_deployment_scale(name="oxauth", replicas=self.settings["OXAUTH_REPLICAS"],
                                                          namespace=self.settings["GLUU_NAMESPACE"])

    def deploy_oxd(self):
        self.kubernetes.create_objects_from_dict(self.oxd_server_yaml)
        if not self.settings["AWS_LB_TYPE"] == "alb":
            self.kubernetes.check_pods_statuses(self.settings["GLUU_NAMESPACE"], "app=oxd-server", self.timeout)
        self.kubernetes.patch_namespaced_deployment_scale(name="oxd-server",
                                                          replicas=self.settings["OXD_SERVER_REPLICAS"],
                                                          namespace=self.settings["GLUU_NAMESPACE"])

    def deploy_casa(self):
        self.kubernetes.create_objects_from_dict(self.casa_yaml)
        if not self.settings["AWS_LB_TYPE"] == "alb":
            self.kubernetes.check_pods_statuses(self.settings["GLUU_NAMESPACE"], "app=casa", self.timeout)
        self.kubernetes.patch_namespaced_deployment_scale(name="casa", replicas=self.settings["CASA_REPLICAS"],
                                                          namespace=self.settings["GLUU_NAMESPACE"])

    def deploy_oxtrust(self):
        self.kubernetes.create_objects_from_dict(self.oxtrust_yaml)
        if not self.settings["AWS_LB_TYPE"] == "alb":
            self.kubernetes.check_pods_statuses(self.settings["GLUU_NAMESPACE"], "app=oxtrust", self.timeout)
        self.kubernetes.patch_namespaced_stateful_set_scale(name="oxtrust", replicas=self.settings["OXTRUST_REPLICAS"],
                                                            namespace=self.settings["GLUU_NAMESPACE"])

    def deploy_oxshibboleth(self):
        self.kubernetes.create_objects_from_dict(self.oxshibboleth_yaml)
        self.kubernetes.check_pods_statuses(self.settings["GLUU_NAMESPACE"], "app=oxshibboleth", self.timeout)
        self.kubernetes.patch_namespaced_stateful_set_scale(name="oxshibboleth",
                                                            replicas=self.settings["OXSHIBBOLETH_REPLICAS"],
                                                            namespace=self.settings["GLUU_NAMESPACE"])

    def deploy_oxpassport(self):
        self.kubernetes.create_objects_from_dict(self.oxpassport_yaml)
        if not self.settings["AWS_LB_TYPE"] == "alb":
            self.kubernetes.check_pods_statuses(self.settings["GLUU_NAMESPACE"], "app=oxpassport", self.timeout)
        self.kubernetes.patch_namespaced_deployment_scale(name="oxpassport",
                                                          replicas=self.settings["OXPASSPORT_REPLICAS"],
                                                          namespace=self.settings["GLUU_NAMESPACE"])

    def deploy_key_rotation(self):
        self.kubernetes.create_objects_from_dict(self.key_rotate_yaml)
        self.kubernetes.check_pods_statuses(self.settings["GLUU_NAMESPACE"], "app=key-rotation", self.timeout)

    def deploy_radius(self):
        self.kubernetes.create_objects_from_dict(self.radius_yaml)
        if not self.settings["AWS_LB_TYPE"] == "alb":
            self.kubernetes.check_pods_statuses(self.settings["GLUU_NAMESPACE"], "app=radius", self.timeout)
        self.kubernetes.patch_namespaced_deployment_scale(name="radius", replicas=self.settings["RADIUS_REPLICAS"],
                                                          namespace=self.settings["GLUU_NAMESPACE"])

    def deploy_cr_rotate(self):
        self.kubernetes.delete_role("gluu-role", self.settings["GLUU_NAMESPACE"])
        self.kubernetes.delete_role_binding("gluu-rolebinding", self.settings["GLUU_NAMESPACE"])
        self.kubernetes.delete_cluster_role_binding("gluu-rolebinding")
        time.sleep(10)
        self.kubernetes.create_objects_from_dict(self.cr_rotate_yaml)

    def copy_configs_before_restore(self):
        self.gluu_secret = self.kubernetes.read_namespaced_secret("gluu", self.settings["GLUU_NAMESPACE"]).data
        self.gluu_config = self.kubernetes.read_namespaced_configmap("gluu", self.settings["GLUU_NAMESPACE"]).data

    def save_a_copy_of_config(self):
        self.kubernetes.patch_or_create_namespaced_secret(name="secret-params", literal=None, value_of_literal=None,
                                                          namespace=self.settings["GLUU_NAMESPACE"],
                                                          data=self.gluu_secret)
        self.kubernetes.patch_or_create_namespaced_configmap(name="config-params",
                                                             namespace=self.settings["GLUU_NAMESPACE"],
                                                             data=self.gluu_config)

    def mount_config(self):
        self.kubernetes.patch_or_create_namespaced_secret(name="gluu", literal=None, value_of_literal=None,
                                                          namespace=self.settings["GLUU_NAMESPACE"],
                                                          data=self.gluu_secret)
        self.kubernetes.patch_or_create_namespaced_configmap(name="gluu",
                                                             namespace=self.settings["GLUU_NAMESPACE"],
                                                             data=self.gluu_config)

    def run_backup_command(self):
        try:
            exec_ldap_command = ["/opt/opendj/bin/import-ldif", "-n", "userRoot",
                                 "-l", "/opt/opendj/ldif/backup-this-copy.ldif",
                                 "--bindPassword", self.settings["LDAP_PW"]]
            self.kubernetes.connect_get_namespaced_pod_exec(exec_command=exec_ldap_command,
                                                            app_label="app=opendj",
                                                            namespace=self.settings["GLUU_NAMESPACE"])
        except:
            pass

    def setup_backup_ldap(self):
        subprocess_cmd("alias kubectl='microk8s.kubectl'")
        encoded_ldap_pw_bytes = base64.b64encode(self.settings["LDAP_PW"].encode("utf-8"))
        encoded_ldap_pw_string = str(encoded_ldap_pw_bytes, "utf-8")
        self.kubernetes.patch_or_create_namespaced_secret(name="ldap-auth",
                                                          namespace=self.settings["GLUU_NAMESPACE"],
                                                          literal="password",
                                                          value_of_literal=encoded_ldap_pw_string)
        kustomize_parser = Parser("ldap/backup/kustomization.yaml", "Kustomization")
        kustomize_parser["namespace"] = self.settings["GLUU_NAMESPACE"]
        kustomize_parser["configMapGenerator"][0]["literals"] = ["GLUU_LDAP_AUTO_REPLICATE=" + self.settings[
            "GLUU_CACHE_TYPE"], "GLUU_CONFIG_KUBERNETES_NAMESPACE=" + self.settings["GLUU_NAMESPACE"],
                                                                 "GLUU_SECRET_KUBERNETES_NAMESPACE=" + self.settings[
                                                                     "GLUU_NAMESPACE"],
                                                                 "GLUU_CONFIG_ADAPTER=kubernetes",
                                                                 "GLUU_SECRET_ADAPTER=kubernetes",
                                                                 "GLUU_LDAP_INIT='true'",
                                                                 "GLUU_LDAP_INIT_HOST='opendj'",
                                                                 "GLUU_LDAP_INIT_PORT='1636'",
                                                                 "GLUU_CERT_ALT_NAME='opendj'",
                                                                 "GLUU_PERSISTENCE_LDAP_MAPPING=" + self.settings[
                                                                     "HYBRID_LDAP_HELD_DATA"],
                                                                 "GLUU_PERSISTENCE_TYPE=" + self.settings[
                                                                     "PERSISTENCE_BACKEND"]]
        kustomize_parser.dump_it()
        cron_job_parser = Parser("ldap/backup/cronjobs.yaml", "CronJob")
        cron_job_parser["spec"]["schedule"] = self.settings["LDAP_BACKUP_SCHEDULE"]
        cron_job_parser.dump_it()
        command = "kubectl kustomize ldap/backup > ./ldap-backup.yaml"
        subprocess_cmd(command)
        self.kubernetes.create_objects_from_dict("./ldap-backup.yaml")

    def upgrade(self):
        self.update_kustomization_yaml()
        self.kustomize_gluu_upgrade()
        self.adjust_fqdn_yaml_entries()
        self.kubernetes.create_objects_from_dict(self.gluu_upgrade_yaml)
        if not self.settings["AWS_LB_TYPE"] == "alb":
            self.kubernetes.check_pods_statuses(self.settings["GLUU_NAMESPACE"], "app=gluu-upgrade", self.timeout)
        casa_image = self.settings["CASA_IMAGE_NAME"] + ":" + self.settings["CASA_IMAGE_TAG"]
        cr_rotate_image = self.settings["CACHE_REFRESH_ROTATE_IMAGE_NAME"] + ":" + self.settings[
            "CACHE_REFRESH_ROTATE_IMAGE_TAG"]
        key_rotate_image = self.settings["KEY_ROTATE_IMAGE_NAME"] + ":" + self.settings["KEY_ROTATE_IMAGE_TAG"]
        ldap_image = self.settings["LDAP_IMAGE_NAME"] + ":" + self.settings["LDAP_IMAGE_TAG"]
        oxauth_image = self.settings["OXAUTH_IMAGE_NAME"] + ":" + self.settings["OXAUTH_IMAGE_TAG"]
        oxd_image = self.settings["OXD_IMAGE_NAME"] + ":" + self.settings["OXD_IMAGE_TAG"]
        oxpassport_image = self.settings["OXPASSPORT_IMAGE_NAME"] + ":" + self.settings["OXPASSPORT_IMAGE_TAG"]
        oxshibboleth_image = self.settings["OXSHIBBOLETH_IMAGE_NAME"] + ":" + self.settings["OXSHIBBOLETH_IMAGE_TAG"]
        oxtrust_image = self.settings["OXTRUST_IMAGE_NAME"] + ":" + self.settings["OXTRUST_IMAGE_TAG"]
        radius_image = self.settings["RADIUS_IMAGE_NAME"] + ":" + self.settings["RADIUS_IMAGE_TAG"]

        self.kubernetes.patch_namespaced_deployment(name="casa",
                                                    image=casa_image, namespace=self.settings["GLUU_NAMESPACE"])
        self.kubernetes.patch_namespaced_daemonset(name="cr-rotate",
                                                   image=cr_rotate_image, namespace=self.settings["GLUU_NAMESPACE"])
        self.kubernetes.patch_namespaced_deployment(name="key-rotation",
                                                    image=key_rotate_image, namespace=self.settings["GLUU_NAMESPACE"])
        self.kubernetes.patch_namespaced_statefulset(name="opendj",
                                                     image=ldap_image, namespace=self.settings["GLUU_NAMESPACE"])
        self.kubernetes.patch_namespaced_deployment(name="oxauth",
                                                    image=oxauth_image, namespace=self.settings["GLUU_NAMESPACE"])
        self.kubernetes.patch_namespaced_deployment(name="oxd-server",
                                                    image=oxd_image, namespace=self.settings["GLUU_NAMESPACE"])
        self.kubernetes.patch_namespaced_deployment(name="oxpassport",
                                                    image=oxpassport_image, namespace=self.settings["GLUU_NAMESPACE"])
        self.kubernetes.patch_namespaced_statefulset(name="oxshibboleth",
                                                     image=oxshibboleth_image,
                                                     namespace=self.settings["GLUU_NAMESPACE"])
        self.kubernetes.patch_namespaced_statefulset(name="oxtrust",
                                                     image=oxtrust_image, namespace=self.settings["GLUU_NAMESPACE"])
        self.kubernetes.patch_namespaced_deployment(name="radius",
                                                    image=radius_image, namespace=self.settings["GLUU_NAMESPACE"])

    def install(self, install_couchbase=True, restore=False):
        self.update_kustomization_yaml()
        if not restore:
            self.kubernetes.create_namespace(name=self.settings["GLUU_NAMESPACE"])
        self.kustomize_config()
        self.kustomize_jackrabbit()
        self.kustomize_ldap()
        self.kustomize_persistence()
        self.kustomize_oxauth()
        self.kustomize_oxtrust()
        self.kustomize_oxshibboleth()
        self.kustomize_oxpassport()
        self.kustomize_key_rotation()
        self.kustomize_cr_rotate()
        self.kustomize_oxd_server()
        self.kustomize_casa()
        self.kustomize_radius()
        self.kustomize_update_lb_ip()
        if self.settings["INSTALL_JACKRABBIT"] == "Y" and not restore:
            self.deploy_jackrabbit()
        if install_couchbase:
            if self.settings["PERSISTENCE_BACKEND"] != "ldap":
                if self.settings["INSTALL_COUCHBASE"] == "Y":
                    couchbase_app = Couchbase(self.settings)
                    couchbase_app.uninstall()
                    couchbase_app = Couchbase(self.settings)
                    couchbase_app.install()
                else:
                    encoded_cb_pass_bytes = base64.b64encode(self.settings["COUCHBASE_PASSWORD"].encode("utf-8"))
                    encoded_cb_pass_string = str(encoded_cb_pass_bytes, "utf-8")
                    couchbase_app = Couchbase(self.settings)
                    couchbase_app.create_couchbase_gluu_cert_pass_secrets(self.settings["COUCHBASE_CRT"],
                                                                          encoded_cb_pass_string)

        if self.settings["DEPLOY_MULTI_CLUSTER"] != "Y" and self.settings["DEPLOY_MULTI_CLUSTER"] != "y":
            if restore:
                self.mount_config()
                self.save_a_copy_of_config()
            else:
                self.deploy_config()

        if not self.settings["AWS_LB_TYPE"] == "alb":
            self.setup_tls()

        if self.settings["INSTALL_REDIS"] == "Y" or self.settings["INSTALL_GLUU_GATEWAY"] == "Y":
            self.deploy_kubedb()

        if self.settings["INSTALL_REDIS"] == "Y":
            self.deploy_redis()

        if self.settings["PERSISTENCE_BACKEND"] == "hybrid" or \
                self.settings["PERSISTENCE_BACKEND"] == "ldap":
            if restore:
                self.run_backup_command()
                self.mount_config()
                self.check_lb()
            else:
                self.deploy_ldap()
                if self.settings["DEPLOYMENT_ARCH"] != "microk8s" and self.settings["DEPLOYMENT_ARCH"] != "minikube":
                    self.setup_backup_ldap()

        if not restore:
            if self.settings["AWS_LB_TYPE"] == "alb":
                self.prepare_alb()
                self.deploy_alb()
            else:
                self.deploy_nginx()
        self.adjust_fqdn_yaml_entries()
        if not restore:
            self.deploy_persistence()

        if self.settings["IS_GLUU_FQDN_REGISTERED"] != "Y" and self.settings["IS_GLUU_FQDN_REGISTERED"] != "y":
            if self.settings["DEPLOYMENT_ARCH"] == "eks":
                self.deploy_update_lb_ip()

        self.deploy_oxauth()
        if self.settings["ENABLE_OXD"] == "Y":
            self.deploy_oxd()

        if self.settings["ENABLE_CASA"] == "Y":
            self.deploy_casa()

        self.deploy_oxtrust()

        if self.settings["ENABLE_OXSHIBBOLETH"] == "Y":
            self.deploy_oxshibboleth()
            if restore:
                self.mount_config()

        if self.settings["ENABLE_OXPASSPORT"] == "Y":
            self.deploy_oxpassport()

        if self.settings["ENABLE_CACHE_REFRESH"] == "Y":
            self.deploy_cr_rotate()

        if self.settings["ENABLE_KEY_ROTATE"] == "Y":
            self.deploy_key_rotation()
            if restore:
                self.mount_config()

        if self.settings["ENABLE_RADIUS"] == "Y":
            self.deploy_radius()

        if self.settings["INSTALL_GLUU_GATEWAY"] == "Y":
            self.install_gluugateway_dbmode()

    def uninstall(self, restore=False):
        gluu_service_names = ["casa", "cr-rotate", "key-rotation", "opendj", "oxauth", "oxpassport",
                              "oxshibboleth", "oxtrust", "radius", "oxd-server", "jackrabbit", "gg-kong-ui"]
        gluu_storage_class_names = ["opendj-sc", "jackrabbit-sc", "postgres-sc", "redis-sc"]
        nginx_service_name = "ingress-nginx"
        gluu_deployment_app_labels = ["app=casa", "app=oxauth", "app=oxd-server", "app=oxpassport",
                                      "app=radius", "app=key-rotation", "app=jackrabbit", "app=gg-ui"]
        nginx_deployemnt_app_name = "nginx-ingress-controller"
        stateful_set_labels = ["app=opendj", "app=oxtrust", "app=oxshibboleth", "app=jackrabbit"]
        jobs_labels = ["app=config-init-load", "app=persistence-load", "app=gluu-upgrade"]
        secrets = ["oxdkeystorecm", "gluu", "tls-certificate"]
        cb_secrets = ["cb-pass", "cb-crt"]
        daemon_set_label = "app=cr-rotate"
        all_labels = gluu_deployment_app_labels + stateful_set_labels + jobs_labels + [daemon_set_label]
        gluu_config_maps_names = ["casacm", "updatelbip", "gluu"]
        nginx_config_maps_names = ["nginx-configuration", "tcp-services", "udp-services"]
        gluu_cluster_role_bindings_name = "cluster-admin-binding"
        nginx_roles_name = "nginx-ingress-role"
        nginx_cluster_role_name = "nginx-ingress-clusterrole"
        nginx_role_bindings_name = "nginx-ingress-role-nisa-binding"
        nginx_cluster_role_bindings_name = "nginx-ingress-clusterrole-nisa-binding"
        nginx_service_account_name = "nginx-ingress-serviceaccount"
        nginx_ingress_extensions_names = ["gluu-ingress-base", "gluu-ingress-openid-configuration",
                                          "gluu-ingress-uma2-configuration", "gluu-ingress-webfinger",
                                          "gluu-ingress-simple-web-discovery", "gluu-ingress-scim-configuration",
                                          "gluu-ingress-fido-u2f-configuration", "gluu-ingress", "gluu-gg-ui",
                                          "gluu-ingress-stateful", "gluu-casa", "gluu-ingress-fido2-configuration"]
        minkube_yamls_folder = Path("./gluuminikubeyamls")
        microk8s_yamls_folder = Path("./gluumicrok8yamls")
        eks_yamls_folder = Path("./gluueksyamls")
        gke_yamls_folder = Path("./gluugkeyamls")
        aks_yamls_folder = Path("./gluuaksyamls")
        if restore:
            gluu_service_names.pop(3)
            gluu_storage_class_names.pop(1)
            stateful_set_labels.pop(0)

        for service in gluu_service_names:
            self.kubernetes.delete_service(service, self.settings["GLUU_NAMESPACE"])
        if not restore:
            if self.settings["INSTALL_REDIS"] == "Y":
                self.uninstall_redis()
            if self.settings["INSTALL_GLUU_GATEWAY"] == "Y":
                self.uninstall_postgres()
                self.uninstall_kong()
                self.kubernetes.delete_namespace(name=self.settings["GG_UI_NAMESPACE"])
            if self.settings["INSTALL_REDIS"] == "Y" or self.settings["INSTALL_GLUU_GATEWAY"] == "Y":
                self.uninstall_kubedb()
            self.kubernetes.delete_service(nginx_service_name, "ingress-nginx")
        for deployment in gluu_deployment_app_labels:
            self.kubernetes.delete_deployment_using_label(self.settings["GLUU_NAMESPACE"], deployment)
        if not restore:
            self.kubernetes.delete_deployment_using_name(nginx_deployemnt_app_name, "ingress-nginx")
        for stateful_set in stateful_set_labels:
            self.kubernetes.delete_stateful_set(self.settings["GLUU_NAMESPACE"], stateful_set)
        for job in jobs_labels:
            self.kubernetes.delete_job(self.settings["GLUU_NAMESPACE"], job)
        for secret in secrets:
            self.kubernetes.delete_secret(secret, self.settings["GLUU_NAMESPACE"])
        if not restore:
            for secret in cb_secrets:
                self.kubernetes.delete_secret(secret, self.settings["GLUU_NAMESPACE"])
        self.kubernetes.delete_daemon_set(self.settings["GLUU_NAMESPACE"], daemon_set_label)
        for config_map in gluu_config_maps_names:
            self.kubernetes.delete_config_map_using_name(config_map, self.settings["GLUU_NAMESPACE"])
        if not restore:
            for config_map in nginx_config_maps_names:
                self.kubernetes.delete_config_map_using_name(config_map, "ingress-nginx")
        for cm_pv_pvc in all_labels:
            self.kubernetes.delete_config_map_using_label(self.settings["GLUU_NAMESPACE"], cm_pv_pvc)
            self.kubernetes.delete_persistent_volume(cm_pv_pvc)
            self.kubernetes.delete_persistent_volume_claim(self.settings["GLUU_NAMESPACE"], cm_pv_pvc)
        for storage_class in gluu_storage_class_names:
            self.kubernetes.delete_storage_class(storage_class)

        if not restore:
            self.kubernetes.delete_role("gluu-role", self.settings["GLUU_NAMESPACE"])
            self.kubernetes.delete_role_binding("gluu-rolebinding", self.settings["GLUU_NAMESPACE"])
            self.kubernetes.delete_role(nginx_roles_name, "ingress-nginx")
            self.kubernetes.delete_cluster_role_binding("gluu-rolebinding")
            self.kubernetes.delete_cluster_role_binding(gluu_cluster_role_bindings_name)
            self.kubernetes.delete_role_binding(nginx_role_bindings_name, "ingress-nginx")
            self.kubernetes.delete_cluster_role_binding(nginx_cluster_role_bindings_name)
            self.kubernetes.delete_service_account(nginx_service_account_name, "ingress-nginx")
            self.kubernetes.delete_cluster_role(nginx_cluster_role_name)
            for extension in nginx_ingress_extensions_names:
                self.kubernetes.delete_ingress(extension)
            self.kubernetes.delete_namespace("ingress-nginx")
            if not self.settings["GLUU_NAMESPACE"] == "default":
                self.kubernetes.delete_namespace(self.settings["GLUU_NAMESPACE"])
        with contextlib.suppress(FileNotFoundError):
            os.remove("oxd-server.keystore")
        with contextlib.suppress(FileNotFoundError):
            os.remove("easyrsa_ca_password")
        if minkube_yamls_folder.exists() or microk8s_yamls_folder.exists():
            shutil.rmtree('/data', ignore_errors=True)
        else:
            for node_ip in self.settings["NODES_IPS"]:
                if self.settings["DEPLOYMENT_ARCH"] == "minikube":
                    subprocess_cmd("minikube ssh 'sudo rm -rf /data'")
                elif self.settings["DEPLOYMENT_ARCH"] == "microk8s":
                    shutil.rmtree('/data', ignore_errors=True)
                else:
                    if self.settings["APP_VOLUME_TYPE"] == 6 or self.settings["APP_VOLUME_TYPE"] == 16:
                        if self.settings["DEPLOYMENT_ARCH"] == "eks":
                            ssh_and_remove(self.settings["NODE_SSH_KEY"], "ec2-user", node_ip, "/data")
                        elif self.settings["DEPLOYMENT_ARCH"] == "aks":
                            ssh_and_remove(self.settings["NODE_SSH_KEY"], "opc", node_ip, "/data")
            if self.settings["APP_VOLUME_TYPE"] == 11:
                if self.settings["DEPLOYMENT_ARCH"] == "gke":
                    for node_name in self.settings["NODES_NAMES"]:
                        for zone in self.settings["NODES_ZONES"]:
                            subprocess_cmd("gcloud compute ssh user@{} --zone={} --command='sudo rm -rf $HOME/opendj'".
                                           format(node_name, zone))
                            subprocess_cmd(
                                "gcloud compute ssh user@{} --zone={} --command='sudo rm -rf $HOME/jackrabbit'".
                                format(node_name, zone))
        if not restore:
            shutil.rmtree(Path("./previousgluuminikubeyamls"), ignore_errors=True)
            shutil.rmtree(Path("./previousgluumicrok8yamls"), ignore_errors=True)
            shutil.rmtree(Path("./previousgluueksyamls"), ignore_errors=True)
            shutil.rmtree(Path("./previousgluuaksyamls"), ignore_errors=True)
            shutil.rmtree(Path("./previousgluugkeyamls"), ignore_errors=True)
            with contextlib.suppress(FileNotFoundError):
                shutil.copytree(minkube_yamls_folder, Path("./previousgluuminikubeyamls"))
            with contextlib.suppress(FileNotFoundError):
                shutil.copytree(microk8s_yamls_folder, Path("./previousgluumicrok8yamls"))
            with contextlib.suppress(FileNotFoundError):
                shutil.copytree(eks_yamls_folder, Path("./previousgluueksyamls"))
            with contextlib.suppress(FileNotFoundError):
                shutil.copytree(aks_yamls_folder, Path("./previousgluuaksyamls"))
            with contextlib.suppress(FileNotFoundError):
                shutil.copytree(gke_yamls_folder, Path("./previousgluugkeyamls"))
            with contextlib.suppress(FileNotFoundError):
                shutil.move(Path("./ingress.crt"), Path("./previous-ingress.crt"))
            with contextlib.suppress(FileNotFoundError):
                shutil.move(Path("./ingress.key"), Path("./previous-ingress.key"))
            with contextlib.suppress(FileNotFoundError):
                time_str = time.strftime("_created_%d-%m-%Y_%H-%M-%S")
                shutil.copy(Path("./settings.json"), Path("./settings" + time_str + ".json"))

    def uninstall_kubedb(self, helm=False):
        if self.settings["DEPLOYMENT_ARCH"] == "gke":
            exec_cmd("kubectl create clusterrolebinding 'cluster-admin-$(whoami)' "
                     "--clusterrole=cluster-admin --user='$(gcloud config get-value core/account)'")

        if self.settings["DEPLOYMENT_ARCH"] == "microk8s" or helm:
            try:
                exec_cmd("helm repo add appscode https://charts.appscode.com/stable/")
                exec_cmd("helm repo update")
                exec_cmd("helm delete kubedb-operator --namespace gluu-kubedb")
                exec_cmd("helm delete kubedb-catalog --namespace gluu-kubedb")
                time.sleep(20)
            except FileNotFoundError:
                logger.error("Helm v3 is not installed. Please install it to continue "
                             "https://helm.sh/docs/intro/install/")
                raise SystemExit(1)
        else:
            exec_cmd("bash -s ./redis/kubedb.sh --uninstall --purge  --namespace=gluu-kubedb")
        self.kubernetes.delete_namespace(name="gluu-kubedb")

    def uninstall_redis(self):
        logger.info("Removing gluu-redis-cluster...")
        try:
            exec_cmd("kubectl delete all -n {} --all".format(self.settings["REDIS_NAMESPACE"]))
        except:
            exec_cmd("microk8s.kubectl delete all -n {} --all".format(self.settings["REDIS_NAMESPACE"]))
        self.kubernetes.delete_namespace(name=self.settings["REDIS_NAMESPACE"])

    def uninstall_kong(self):
        logger.info("Removing gluu gateway kong...")
        kong_all_in_one_db = Path("./gluu-gateway-ui/kong-all-in-one-db.yaml")
        try:
            exec_cmd("kubectl delete -f {}".format(kong_all_in_one_db))
        except:
            exec_cmd("microk8s.kubectl delete -f {}".format(kong_all_in_one_db))
        self.kubernetes.delete_namespace(name=self.settings["KONG_NAMESPACE"])

    def uninstall_postgres(self):
        logger.info("Removing gluu-postgres...")
        try:
            exec_cmd("kubectl delete all -n {} --all".format(self.settings["POSTGRES_NAMESPACE"]))
        except:
            exec_cmd("microk8s.kubectl delete all -n {} --all".format(self.settings["POSTGRES_NAMESPACE"]))
        self.kubernetes.delete_namespace(name=self.settings["POSTGRES_NAMESPACE"])


def create_parser():
    """Create parser to handle arguments from CLI.
    """
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(title="Commands", dest="subparser_name")
    subparsers.add_parser("generate-settings", help="Generate settings.json to install "
                                                    "Gluu Enterprise Edition non-interactively")
    subparsers.add_parser("install", help="Install Gluu Enterprise Edition")
    subparsers.add_parser("install-no-wait", help="Install Gluu Enterprise Edition. "
                                                  "There will be no wait time between installing services. "
                                                  "Pods may look like they are restarting but they will be waiting for hierarchy "
                                                  "pods to be running")
    subparsers.add_parser("install-ldap-backup", help="Install ldap backup cronjob only.")
    subparsers.add_parser("install-gg-dbmode", help="Install Gluu Gateway with Postgres database")
    subparsers.add_parser("restore", help="Install Gluu Enterprise Edition with a "
                                          "running database and previous configuration")
    subparsers.add_parser("uninstall", help="Uninstall Gluu")
    subparsers.add_parser("upgrade", help="Upgrade Gluu Enterprise Edition")
    subparsers.add_parser("install-couchbase", help="Install Couchbase only. Used with installation of Gluu with Helm")
    subparsers.add_parser("install-couchbase-backup", help="Install Couchbase backup only.")
    subparsers.add_parser("uninstall-couchbase", help="Uninstall Couchbase only.")
    subparsers.add_parser("helm-install", help="Install Gluu Enterprise Edition using helm. "
                                               "This also installs the nginx-ingress chart")
    subparsers.add_parser("helm-uninstall", help="Uninstall Gluu Enterprise Edition using helm. "
                                                 "This also uninstalls the nginx-ingress chart")
    subparsers.add_parser("helm-install-gluu", help="Install Gluu Enterprise Edition using helm. "
                                                    "This assumes nginx-ingress is installed")
    subparsers.add_parser("helm-uninstall-gluu", help="Uninstall Gluu Enterprise Edition using helm. "
                                                      "This only uninstalls Gluu")
    # TODO: Remove the following as soon as the update secret is moved to backend
    subparsers.add_parser("update-secret", help="Update Gluu secret. Often used to update certificates and keys. ")
    # End of section to be removed. TODO
    return parser


def main():
    parser = create_parser()
    args = parser.parse_args(sys.argv[1:])

    if not args.subparser_name:
        parser.print_help()
        return
    # TODO: Remove the following as soon as the update secret is moved to backend
    if args.subparser_name == "update-secret":
        modify_secret()
        return
    # End of section to be removed. TODO
    copy_templates()
    prompts = Prompt()
    settings = prompts.check_settings_and_prompt

    timeout = 300
    if args.subparser_name == "install-no-wait":
        timeout = 0
    try:
        if args.subparser_name == "install" or args.subparser_name == "install-no-wait":
            app = App(settings, timeout)
            app.uninstall()
            app.install()

        if args.subparser_name == "install-ldap-backup":
            app = App(settings)
            app.setup_backup_ldap()

        elif args.subparser_name == "uninstall":
            logger.info("Removing all Gluu resources...")
            app = App(settings)
            app.uninstall()

        elif args.subparser_name == "upgrade":
            logger.info("Starting upgrade...")
            settings = prompts.prompt_upgrade
            app = App(settings)
            app.upgrade()

        elif args.subparser_name == "restore":
            app = App(settings)
            app.copy_configs_before_restore()
            app.uninstall(restore=True)
            app.install(install_couchbase=False, restore=True)

        elif args.subparser_name == "install-couchbase":
            settings = prompts.prompt_couchbase()
            couchbase = Couchbase(settings)
            couchbase.install()

        elif args.subparser_name == "install-couchbase-backup":
            settings = prompts.prompt_couchbase()
            couchbase = Couchbase(settings)
            couchbase.setup_backup_couchbase()

        elif args.subparser_name == "uninstall-couchbase":
            settings = prompts.prompt_couchbase()
            couchbase = Couchbase(settings)
            couchbase.uninstall()

        elif args.subparser_name == "install-gg-dbmode":
            app = App(settings)
            prompts.prompt_gluu_gateway()
            app.install_gluugateway_dbmode()

        elif args.subparser_name == "generate-settings":
            logger.info("settings.json has been generated")

        elif args.subparser_name == "helm-install":
            settings = prompts.prompt_helm
            if settings["INSTALL_REDIS"] == "Y":
                app = App(settings)
                app.uninstall_redis()
                app.uninstall_kubedb(helm=True)
                app.deploy_kubedb(helm=True)
                app.deploy_redis()
            helm = Helm(settings)
            helm.install_gluu()

        elif args.subparser_name == "helm-uninstall":
            settings = prompts.prompt_helm
            helm = Helm(settings)
            helm.uninstall_gluu()
            helm.uninstall_nginx_ingress()
            if settings["INSTALL_REDIS"] == "Y":
                app = App(settings)
                app.uninstall_redis()
                app.uninstall_kubedb(helm=True)
                time.sleep(30)
                app.uninstall()

        elif args.subparser_name == "helm-install-gluu":
            settings = prompts.prompt_helm
            helm = Helm(settings)
            helm.uninstall_gluu()
            helm.install_gluu(install_ingress=False)

        elif args.subparser_name == "helm-uninstall-gluu":
            settings = prompts.prompt_helm
            helm = Helm(settings)
            helm.uninstall_gluu()

    except KeyboardInterrupt:
        print("\n[I] Canceled by user; exiting ...")


if __name__ == "__main__":
    main()
