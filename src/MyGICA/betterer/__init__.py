import asyncio
import os
import subprocess
import sys
from asyncio import StreamReader
from pathlib import Path
from subprocess import CompletedProcess
from typing import TextIO, Union


def subprocess_run(
        command: list,
        stdout_log_path: Union[str, Path] = None,
        stderr_log_path: Union[str, Path] = None,
        stream_terminal: bool = True,
        text: bool = True,  # 新增参数，是否以文本形式返回 stdout/stderr
        encoding: str = 'utf-8',  # 新增参数，指定编码
        errors: str = 'show'  # 'ignore'
) -> CompletedProcess[str]:
    return asyncio.run(run_with_log(
        command=command,
        stdout_log_path=stdout_log_path,
        stderr_log_path=stderr_log_path,
        stream_terminal=stream_terminal,
        text=text,
        encoding=encoding,
        errors=errors,
    ))


async def run_with_log(
        command: list,
        stdout_log_path: Union[str, Path] = None,
        stderr_log_path: Union[str, Path] = None,
        stream_terminal: bool = True,
        text: bool = True,  # 新增参数，是否以文本形式返回 stdout/stderr
        encoding: str = 'utf-8',  # 新增参数，指定编码
        errors: str = 'show'  # 'ignore'
) -> CompletedProcess[str]:
    """
    异步运行命令并实时输出到终端，同时将 stdout 和 stderr 日志保存到文件
    兼容 subprocess.run(...) 的返回值格式
    """

    # 缓存输出内容
    stdout_buffer = bytearray()
    stderr_buffer = bytearray()

    env = os.environ.copy()
    env['PYTHONIOENCODING'] = encoding  # 强制子进程使用 UTF-8

    # 创建子进程
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        bufsize=0,  # 禁用缓冲，提高实时性
        env=env,  # 传递修改后的环境变量
    )

    async def read_stream(stream: StreamReader, io: TextIO, buffer: bytearray) -> None:
        last = bytearray()
        while True:
            chunk = await stream.read(1)  # 必须逐字节，才能把 ffmpeg 的原生交互提问实时透传到终端
            if not chunk:  # EOF
                break
            last += chunk
            buffer += chunk
            # 解码尽可能多的完整UTF-8字符
            decoded, last = decode_utf8_with_remaining(last, encoding)
            if decoded == '\r':
                last = b'\r' + last  # 保留回车符以支持进度条
                continue

            # 输出到终端，缓存到内存
            if decoded and stream_terminal:
                io.write(decoded)
                io.flush()

    tasks = [
        asyncio.create_task(read_stream(process.stdout, sys.stdout, stdout_buffer)),
        asyncio.create_task(read_stream(process.stderr, sys.stderr, stderr_buffer)),
    ]
    await asyncio.gather(*tasks)

    # 等待子进程结束
    return_code = await process.wait()

    # 成功后再写入日志文件
    if return_code == 0:
        if stdout_log_path and stdout_buffer:
            Path(stdout_log_path).write_bytes(stdout_buffer)
        if stderr_log_path and stderr_buffer:
            Path(stderr_log_path).write_bytes(stderr_buffer)
    else:
        print(f"[{run_with_log.__name__}]命令执行失败，跳过写入日志文件")

    # 根据 text 参数决定返回 str 还是 bytes
    out_data = stdout_buffer.decode(encoding) if text else bytes(stdout_buffer)
    err_data = stderr_buffer.decode(encoding) if text else bytes(stderr_buffer)

    # 返回 CompletedProcess 兼容对象
    result = CompletedProcess(
        args=command,
        returncode=return_code,
        stdout=out_data,
        stderr=err_data
    )

    if errors != 'ignore' and return_code != 0:
        raise subprocess.CalledProcessError(
            returncode=return_code,
            cmd=command,
            output=out_data,
            stderr=err_data
        )

    return result


def decode_utf8_with_remaining(data: bytearray, encoding: str = 'utf-8') -> tuple[str, bytearray]:
    """解码尽可能多的完整 UTF-8 字符，返回剩余字节"""
    for i in range(len(data), -1, -1):
        try:
            return data[:i].decode(encoding=encoding), data[i:]
        except UnicodeDecodeError:
            continue
    return "", data


# 示例调用
if __name__ == "__main__":
    pass
