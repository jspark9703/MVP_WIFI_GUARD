"""Estimate monthly AWS operating cost for the WIFI-GUARD model pipeline.

The defaults are a planning model for ap-northeast-2, not an AWS quote. Unit
prices are copied from the public AWS Price List bulk files and every modeled
scenario keeps usage assumptions separate from those prices.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path


PRICING_AS_OF = "2026-09-23"
REGION = "ap-northeast-2"


@dataclass(frozen=True)
class Price:
    usd: float
    unit: str
    effective_date: str
    source: str


PRICES = {
    "ec2_g4dn_xlarge_ondemand_hour": Price(
        0.647,
        "instance-hour",
        "2026-09-01",
        "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/ap-northeast-2/index.csv",
    ),
    "ec2_g4dn_xlarge_1yr_no_upfront_hour": Price(
        0.407,
        "instance-hour",
        "2026-09-21",
        "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/ap-northeast-2/index.csv",
    ),
    "ebs_gp3_gb_month": Price(
        0.0912,
        "GB-month",
        "2026-09-01",
        "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/ap-northeast-2/index.csv",
    ),
    "rds_postgresql_t4g_medium_single_hour": Price(
        0.102,
        "instance-hour",
        "2026-09-01",
        "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonRDS/current/ap-northeast-2/index.csv",
    ),
    "rds_postgresql_t4g_medium_multi_hour": Price(
        0.203,
        "instance-hour",
        "2026-09-01",
        "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonRDS/current/ap-northeast-2/index.csv",
    ),
    "rds_postgresql_gp3_single_gb_month": Price(
        0.131,
        "GB-month",
        "2026-09-01",
        "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonRDS/current/ap-northeast-2/index.csv",
    ),
    "rds_postgresql_gp3_multi_gb_month": Price(
        0.262,
        "GB-month",
        "2026-09-01",
        "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonRDS/current/ap-northeast-2/index.csv",
    ),
    "s3_standard_gb_month": Price(
        0.025,
        "GB-month",
        "2026-09-01",
        "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/ap-northeast-2/index.csv",
    ),
    "msk_serverless_cluster_hour": Price(
        0.92,
        "cluster-hour",
        "2026-07-01",
        "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonMSK/current/ap-northeast-2/index.csv",
    ),
    "msk_serverless_partition_hour": Price(
        0.0018,
        "partition-hour",
        "2026-07-01",
        "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonMSK/current/ap-northeast-2/index.csv",
    ),
    "msk_serverless_data_in_gb": Price(
        0.123,
        "GB ingressed",
        "2026-07-01",
        "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonMSK/current/ap-northeast-2/index.csv",
    ),
    "msk_serverless_data_out_gb": Price(
        0.061,
        "GB egressed",
        "2026-07-01",
        "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonMSK/current/ap-northeast-2/index.csv",
    ),
    "msk_serverless_storage_gb_month": Price(
        0.114,
        "GB-month",
        "2026-07-01",
        "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonMSK/current/ap-northeast-2/index.csv",
    ),
    "cloudwatch_custom_logs_ingest_gb": Price(
        0.76,
        "GB ingested",
        "2026-09-01",
        "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonCloudWatch/current/ap-northeast-2/index.csv",
    ),
}


SCENARIOS = {
    "pilot_all_in_one": {
        "label": "Pilot all-in-one",
        "description": "One GPU EC2 node runs inference, Kafka, PostgreSQL, MLflow, and monitoring; no HA.",
        "gpu_nodes": 1,
        "ebs_gp3_gb": 100,
        "rds_mode": None,
        "rds_gp3_gb": 0,
        "s3_gb": 100,
        "cloudwatch_ingest_gb": 10,
        "msk": None,
        "network_and_requests_allowance_usd": 25,
    },
    "managed_baseline": {
        "label": "Managed baseline",
        "description": "One GPU EC2 node with RDS Single-AZ and MSK Serverless; inference is not HA.",
        "gpu_nodes": 1,
        "ebs_gp3_gb": 100,
        "rds_mode": "single",
        "rds_gp3_gb": 100,
        "s3_gb": 100,
        "cloudwatch_ingest_gb": 15,
        "msk": {"clusters": 1, "partitions": 3, "data_in_gb": 10, "data_out_gb": 10, "storage_gb": 50},
        "network_and_requests_allowance_usd": 50,
    },
    "managed_ha": {
        "label": "Managed HA",
        "description": "Two GPU EC2 nodes, RDS Multi-AZ, and MSK Serverless with larger retention/traffic assumptions.",
        "gpu_nodes": 2,
        "ebs_gp3_gb": 200,
        "rds_mode": "multi",
        "rds_gp3_gb": 100,
        "s3_gb": 250,
        "cloudwatch_ingest_gb": 50,
        "msk": {"clusters": 1, "partitions": 12, "data_in_gb": 100, "data_out_gb": 100, "storage_gb": 250},
        "network_and_requests_allowance_usd": 100,
    },
}


PIPELINE_STAGES = {
    "edge_capture": "ESP32/Raspberry Pi capture and MQTT uplink (edge hardware; AWS cost excluded)",
    "application_compute": "MQTT ingress, API, inference, training/finetuning, MLflow, and self-managed services",
    "streaming": "Kafka event transport and retention",
    "database": "PostgreSQL metadata, inference results, and service state",
    "artifact_storage": "Datasets, checkpoints, model artifacts, and backups",
    "observability": "Application/resource logs and operational monitoring",
    "shared_network": "Load balancing, DNS, public/private transfer, and API request allowance",
}


def money(value: float) -> float:
    return round(value, 2)


def component(name: str, quantity: float, price_key: str, pipeline_stage: str) -> dict[str, object]:
    price = PRICES[price_key]
    return {
        "name": name,
        "quantity": quantity,
        "unit_price_usd": price.usd,
        "unit": price.unit,
        "monthly_usd": money(quantity * price.usd),
        "price_key": price_key,
        "pipeline_stage": pipeline_stage,
    }


def calculate_scenario(config: dict[str, object], hours: float, usd_krw: float, contingency: float) -> dict[str, object]:
    components: list[dict[str, object]] = []
    components.append(component("EC2 g4dn.xlarge On-Demand", float(config["gpu_nodes"]) * hours, "ec2_g4dn_xlarge_ondemand_hour", "application_compute"))
    components.append(component("EBS gp3", float(config["ebs_gp3_gb"]), "ebs_gp3_gb_month", "application_compute"))

    rds_mode = config["rds_mode"]
    if rds_mode == "single":
        components.append(component("RDS PostgreSQL db.t4g.medium Single-AZ", hours, "rds_postgresql_t4g_medium_single_hour", "database"))
        components.append(component("RDS PostgreSQL gp3 Single-AZ", float(config["rds_gp3_gb"]), "rds_postgresql_gp3_single_gb_month", "database"))
    elif rds_mode == "multi":
        components.append(component("RDS PostgreSQL db.t4g.medium Multi-AZ", hours, "rds_postgresql_t4g_medium_multi_hour", "database"))
        components.append(component("RDS PostgreSQL gp3 Multi-AZ", float(config["rds_gp3_gb"]), "rds_postgresql_gp3_multi_gb_month", "database"))

    components.append(component("S3 Standard", float(config["s3_gb"]), "s3_standard_gb_month", "artifact_storage"))
    billable_logs = max(0.0, float(config["cloudwatch_ingest_gb"]) - 5.0)
    log_component = component("CloudWatch custom logs after 5 GB free tier", billable_logs, "cloudwatch_custom_logs_ingest_gb", "observability")
    log_component["input_gb"] = config["cloudwatch_ingest_gb"]
    components.append(log_component)

    msk = config["msk"]
    if isinstance(msk, dict):
        components.extend(
            [
                component("MSK Serverless cluster-hours", float(msk["clusters"]) * hours, "msk_serverless_cluster_hour", "streaming"),
                component("MSK Serverless partition-hours", float(msk["partitions"]) * hours, "msk_serverless_partition_hour", "streaming"),
                component("MSK Serverless data in", float(msk["data_in_gb"]), "msk_serverless_data_in_gb", "streaming"),
                component("MSK Serverless data out", float(msk["data_out_gb"]), "msk_serverless_data_out_gb", "streaming"),
                component("MSK Serverless storage", float(msk["storage_gb"]), "msk_serverless_storage_gb_month", "streaming"),
            ]
        )

    allowance = float(config["network_and_requests_allowance_usd"])
    components.append(
        {
            "name": "Network, load balancing, DNS, and API request allowance",
            "quantity": 1,
            "unit_price_usd": allowance,
            "unit": "planning allowance/month",
            "monthly_usd": money(allowance),
            "price_key": None,
            "pipeline_stage": "shared_network",
        }
    )
    base = sum(float(row["monthly_usd"]) for row in components)
    buffered = base * (1 + contingency)
    days = hours / 24
    for row in components:
        row["average_daily_usd"] = money(float(row["monthly_usd"]) / days)
        row["share_of_monthly_base_percent"] = round(float(row["monthly_usd"]) / base * 100, 2)
    return {
        "label": config["label"],
        "description": config["description"],
        "components": components,
        "monthly_base_usd": money(base),
        "monthly_with_contingency_usd": money(buffered),
        "monthly_with_contingency_krw": round(buffered * usd_krw),
        "average_daily_base_usd": money(base / days),
        "average_daily_with_contingency_usd": money(buffered / days),
        "average_daily_with_contingency_krw": round(buffered * usd_krw / days),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hours-per-month", type=float, default=730)
    parser.add_argument("--usd-krw", type=float, default=1400)
    parser.add_argument("--contingency-percent", type=float, default=15)
    args = parser.parse_args()
    if args.hours_per_month <= 0:
        raise ValueError("hours-per-month must be positive")
    if args.usd_krw <= 0:
        raise ValueError("usd-krw must be positive")
    if args.contingency_percent < 0:
        raise ValueError("contingency-percent must be non-negative")

    contingency = args.contingency_percent / 100
    scenarios = {
        key: calculate_scenario(value, args.hours_per_month, args.usd_krw, contingency)
        for key, value in SCENARIOS.items()
    }
    on_demand_gpu_month = PRICES["ec2_g4dn_xlarge_ondemand_hour"].usd * args.hours_per_month
    reserved_gpu_month = PRICES["ec2_g4dn_xlarge_1yr_no_upfront_hour"].usd * args.hours_per_month
    result = {
        "status": "planning-estimate",
        "pricing_as_of": PRICING_AS_OF,
        "region": REGION,
        "currency": "USD",
        "planning_exchange_rate": {"usd_krw": args.usd_krw, "type": "assumption-not-live-quote"},
        "hours_per_month": args.hours_per_month,
        "average_days_per_month": round(args.hours_per_month / 24, 4),
        "contingency_percent": args.contingency_percent,
        "calculation_formula": {
            "component_monthly_usd": "quantity_per_month * official_unit_price_usd",
            "scenario_monthly_base_usd": "sum(component_monthly_usd)",
            "scenario_monthly_with_contingency_usd": "scenario_monthly_base_usd * (1 + contingency_percent / 100)",
            "average_daily_usd": "monthly_usd / (hours_per_month / 24)",
            "krw": "usd * planning_exchange_rate.usd_krw",
        },
        "pipeline_stages": PIPELINE_STAGES,
        "capacity_basis": {
            "measured_envelope": "3 vCPU, 4 GiB RAM, 2 GiB GPU VRAM, 10 GiB persistent storage",
            "selected_gpu_instance": "g4dn.xlarge (4 vCPU, 16 GiB RAM, 1 x 16 GiB T4 GPU)",
            "reason": "It exceeds the measured 1.5x envelope and is available in ap-northeast-2.",
        },
        "unit_prices": {
            key: {
                "usd": price.usd,
                "unit": price.unit,
                "effective_date": price.effective_date,
                "source": price.source,
            }
            for key, price in PRICES.items()
        },
        "scenarios": scenarios,
        "sensitivity": {
            "gpu_1yr_no_upfront": {
                "monthly_on_demand_usd": money(on_demand_gpu_month),
                "monthly_reserved_compute_usd": money(reserved_gpu_month),
                "monthly_compute_saving_usd": money(on_demand_gpu_month - reserved_gpu_month),
                "saving_percent": round((1 - reserved_gpu_month / on_demand_gpu_month) * 100, 1),
                "caveat": "Commitment discount applies only after workload stability and contract terms are accepted.",
            },
            "separate_training_10_hours": {
                "additional_ec2_usd": money(10 * PRICES["ec2_g4dn_xlarge_ondemand_hour"].usd),
                "caveat": "Excludes training storage, data transfer, orchestration, and idle setup time.",
            },
        },
        "limitations": [
            "This is a planning estimate, not a bill or AWS Pricing Calculator quote.",
            "Network/request allowances are assumptions because topology, traffic, NAT usage, and public egress are not yet fixed.",
            "The all-in-one pilot has a single failure domain and self-managed Kafka/PostgreSQL operations.",
            "The measured profile is a bounded single-device/smoke workload; device concurrency and retention growth are not load-tested.",
            "Taxes, Enterprise Support, Savings Plans, Spot interruptions, and currency movement are excluded.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
