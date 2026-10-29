"""只向 PyPI 查询已安装包的公开名称/版本，不上传源码或环境变量。"""
from concurrent.futures import ThreadPoolExecutor
from importlib.metadata import distributions
import argparse
import json
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


def inspect_package(package):
    name, version = package
    url = "https://pypi.org/pypi/" + quote(name, safe="") + "/" + quote(version, safe="") + "/json"
    result = {"name": name, "version": version, "source": url}
    try:
        with urlopen(Request(url, headers={"User-Agent": "ElfinRobotStudio-dependency-audit"}), timeout=15) as response:
            metadata = json.load(response)
        if not isinstance(metadata.get("vulnerabilities"), list):
            raise ValueError("PyPI 缺少漏洞记录字段")
        result["vulnerabilities"] = metadata["vulnerabilities"]
    except Exception as exc:
        # 正确：查询失败是未完成，不冒充零漏洞，不输出代理或凭据配置。
        result["error"] = type(exc).__name__
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "output/checks/python-dependency-audit.json")
    args = parser.parse_args()
    packages = sorted({(d.metadata["Name"], d.version) for d in distributions() if d.metadata["Name"]})
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(inspect_package, packages))
    errors = [item["name"] for item in results if "error" in item]
    affected = [item["name"] for item in results if item.get("vulnerabilities")]
    report = {"scope": "installed_environment_including_optional_and_retired_packages",
              "packages": results, "queried": len(results), "errors": errors, "affected": affected,
              "limitations": "已知漏洞数据不证明无未知漏洞，也不证明任意平台可干净安装。"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"queried": len(results), "errors": errors, "affected": affected, "report": str(args.output)}, ensure_ascii=False))
    return 1 if errors or affected else 0


if __name__ == "__main__":
    raise SystemExit(main())
