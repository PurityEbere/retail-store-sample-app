import pulumi
import pulumi_awsx as awsx
import pulumi_eks as eks
import pulumi_kubernetes as k8s

config = pulumi.Config()
min_cluster_size = config.get_int("minClusterSize", 1)
max_cluster_size = config.get_int("maxClusterSize", 2)
desired_cluster_size = config.get_int("desiredClusterSize", 2)
eks_node_instance_type = config.get("eksNodeInstanceType", "t3.medium")
vpc_network_cidr = config.get("vpcNetworkCidr", "10.0.0.0/16")

eks_vpc = awsx.ec2.Vpc("eks-vpc",
    enable_dns_hostnames=True,
    cidr_block=vpc_network_cidr)

eks_cluster = eks.Cluster("eks-cluster",
    vpc_id=eks_vpc.vpc_id,
    authentication_mode=eks.AuthenticationMode.API,
    public_subnet_ids=eks_vpc.public_subnet_ids,
    private_subnet_ids=eks_vpc.private_subnet_ids,
    instance_type=eks_node_instance_type,
    desired_capacity=desired_cluster_size,
    min_size=min_cluster_size,
    max_size=max_cluster_size,
    node_associate_public_ip_address=False,
    endpoint_private_access=False,
    endpoint_public_access=True
)

# Export values to use elsewhere
pulumi.export("kubeconfig", eks_cluster.kubeconfig)
pulumi.export("vpcId", eks_vpc.vpc_id)


k8s_provider = k8s.Provider(
    "k8s-provider",
    kubeconfig=eks_cluster.kubeconfig
)

namespace_name = "retailstore"

namespace = k8s.core.v1.Namespace(
    namespace_name,
    metadata={ "name": "app" },
    opts=pulumi.ResourceOptions(provider=k8s_provider)
)

container_repository = awsx.ecr.Repository("retailstore_repository")
pulumi.export("repository_url", container_repository.url)

images = ["app-orders", "app-catalog", "app-cart", "ui-ui", "app-checkout"]

# Build and push all images
built_images = {}
for image in images:
    app_dir = "../src/" + image.split("-")[1]

    built_image = awsx.ecr.Image(
        image,
        repository_url=container_repository.url,
        context=app_dir,
        platform="linux/amd64",
        opts=pulumi.ResourceOptions(
            ignore_changes=["provider"]
        )
    )
    built_images[image] = built_image

'''
cd ../src
for dir in cart catalog checkout orders ui; do \
  ( \
    cd $dir && \
    echo "converting for $dir" && \
    mkdir -p kompose_files && \
    kompose convert --out kompose_files && \
    KUBECONFIG=/home/purity/retailstore/pulumi-start-aws/kubeconfig.yaml && \
    kubectl apply -f kompose_files -n namespace_name \
  ); \
done
'''
