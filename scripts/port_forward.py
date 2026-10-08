"""TCP 端口转发器：让局域网设备访问 Docker 发布的服务。

背景：Docker Desktop (WSL2) 的端口发布只覆盖 Windows loopback（localhost 中继），
网卡 IP / 局域网没有监听。本脚本在 0.0.0.0 上监听以下端口并中转到 127.0.0.1，
使局域网和本机网卡 IP 都能访问容器内服务。由计划任务 epay-next-portproxy 托管
（登录自启、崩溃自动重启）。

转发列表（用户侧端口 -> 各 docker-compose 的宿主机发布端口）：
  - 8000 -> 127.0.0.1:8001  epay-next（宣传页/管理后台/商户中心）
  - 8088 -> 127.0.0.1:8089  另一支付项目

注意：Docker 发布的宿主机端口会被 WSL2 loopback 中继占用（netstat 不可见），
导致同端口 0.0.0.0 绑定失败（10048）且不对外——所以 docker 发布端口(8001/8089)
与对外端口(8000/8088)必须错开。
"""
import asyncio
import sys

FORWARDS = [
    (8000, 8001),
    (8088, 8089),
]


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def _handle(target_port: int, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        tr, tw = await asyncio.open_connection("127.0.0.1", target_port)
    except Exception:
        try:
            writer.close()
        except Exception:
            pass
        return
    await asyncio.gather(_pipe(reader, tw), _pipe(tr, writer))


async def main() -> None:
    servers = []
    for listen_port, target_port in FORWARDS:
        server = await asyncio.start_server(
            lambda r, w, tp=target_port: _handle(tp, r, w), "0.0.0.0", listen_port
        )
        print(f"forwarding 0.0.0.0:{listen_port} -> 127.0.0.1:{target_port}", flush=True)
        servers.append(server)
    await asyncio.gather(*(s.serve_forever() for s in servers))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
