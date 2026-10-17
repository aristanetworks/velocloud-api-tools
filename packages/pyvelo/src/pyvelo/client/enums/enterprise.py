from enum import StrEnum

from pyvelo.core.enums import NetworkSegmentType

__all__ = ["NetworkSegmentType"]  # plus the local StrEnums below


class EnterpriseServiceType(StrEnum):
    AUTHENTICATION = "authentication"
    DNS = "dns"
    EDGE_HUB_CLUSTER_MEMBER = "edgeHubClusterMember"
    EDGE_HUB_CLUSTER = "edgeHubCluster"
    EDGE_BRANCH_CLUSTER = "edgeBranchCluster"
    EDGE_BRANCH_CLUSTER_MEMBER = "edgeBranchClusterMember"
    EDGE_HUB = "edgeHub"
    CLOUD_SECURITY_SERVICE = "cloudSecurityService"
    SYMANTEC_WSS_INTEGRATION = "wssIntegration"
    NETFLOW_COLLECTOR = "netflowCollector"
    NETFLOW_FILTER = "netflowFilter"
    LOCAL_UI_CREDENTIALS = "localUiCredentials"
    IAAS_SUBSCRIPTION = "iaasSubscription"
    NVS_VIA_EDGE_SERVICE = "nvsViaEdgeService"
    DATA_CENTER = "dataCenter"
    SECURITY_VNF = "securityVnf"
    SECURITY_VNF_SERVICE = "securityVnfService"
    SECURITY_VNF_LICENSE = "securityVnfLicense"
    VNF_IMAGE = "vnfImage"
    CLOUD_WEB_SECURITY = "cloudWebSecurity"
    CLOUD_WEB_SECURITY_POLICY = "cloudWebSecurityPolicy"
    PRIVATE_NETWORK = "privateNetwork"
    PREFIX_DELEGATION_TAGS = "prefixTag"
    LOG_EXPORT_END_POINT = "logExportEndpoint"
    EDGE_BRANCH_TO_BRANCH = "edgeToEdgeBridge"
    BACKHAUL_EDGE = "backHaulEdge"
    CLOUD_TO_CLOUD_INTERCONNECT = "cloudToCloudInterConnect"
    TACACS = "tacacs"


class EnterpriseServicesParamsWith(StrEnum):
    PROFILE_COUNT = "profileCount"
    EDGE_USAGE = "edgeUsage"
    CONFIGURATION = "configuration"
    GROUP_COUNT = "groupCount"
    EDGE_QOS_USAGE = "edgeQOSUsage"
    GATEWAYS = "gateways"
    WSS_USAGE = "wssUsage"


class EnterpriseNetworkSegmentsParamsWith(StrEnum):
    PROFILE_COUNT = "profileCount"
    HANDOFF_USAGE = "handoffUsage"
    MPG_USAGE = "mpgUsage"


class EnterpriseCustomAppsParamsWith(StrEnum):
    PROFILE_LEVEL_USAGE = "profileLevelUsage"
    EDGE_LEVEL_USAGE = "edgeLevelUsage"


class ObjectGroupType(StrEnum):
    ADDRESS_GROUP = "address_group"
    PORT_GROUP = "port_group"


class SecurityObjectType(StrEnum):
    SECURITY_SERVICE_GROUP = "securityServiceGroup"
    URL_CATEGORY_FILTERING = "urlCategoryFiltering"
    URL_REPUTATION_FILTERING = "urlReputationFiltering"
    MALICIOUS_IP_FILTERING = "maliciousIpFiltering"
    IDPS = "idps"


class BgpFilterMatchType(StrEnum):
    COMMUNITY = "COMMUNITY"
    AS_PATH = "AS_PATH"
    PREFIX = "PREFIX"


class BgpFilterActionType(StrEnum):
    NONE = "NONE"
    COMMUNITY = "COMMUNITY"
    LOCAL_PREF = "LOCAL_PREF"
    MED = "MED"
    AS_PATH = "AS_PATH"


class BgpFilterPermitDeny(StrEnum):
    PERMIT = "PERMIT"
    DENY = "DENY"


class CustomAppStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class GetEnterpriseWith(StrEnum):
    ENTERPRISE_PROXY = "enterpriseProxy"


class GetEnterpriseNetworkAllocationWith(StrEnum):
    EDGES = "edges"
    EDGE_COUNT = "edgeCount"
    PROFILE_COUNT = "profileCount"
    PROFILES = "profiles"
    ASSIGNABLE_VLANS = "assignableVlans"


class GetEnterpriseOperatorConfigurationWith(StrEnum):
    WITH = "with"


class GetEnterpriseSecurityObjectsWith(StrEnum):
    PROFILES = "profiles"
    SECURITY_GROUPS = "securityGroups"
