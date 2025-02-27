#
# Copyright (c) 2021 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

#!/usr/bin/env python3

from __future__ import print_function

import numpy as np
import requests, argparse, time, grpc, cv2, asyncio, functools

import tensorflow as tf
from tensorflow_serving.apis import predict_pb2
from tensorflow_serving.apis import prediction_service_pb2_grpc

from utils import *

class benchmark_engine(object):
    def __init__(self,
                 url,
                 image_flag=None,
                 root_cert=None,
                 private_key=None,
                 certificate_chain=None,
                 batch_size=1,
                 concurrent_num=64,
                 response_time=10):
        self.url = url
        self.image_flag = image_flag
        self.batch_size = batch_size
        self.response_time = response_time
        self.concurrent_num = concurrent_num
        self.root_cert = None if not root_cert else open(root_cert, 'rb').read()
        self.private_key = None if not private_key else open(private_key, 'rb').read()
        self.certificate_chain = None if not certificate_chain else open(certificate_chain, 'rb').read()
        self.request_signatures = []
        self.request_stubs = []
        self.__prepare__()

    def __prepare__(self):
        for idx in range(self.concurrent_num):
            # get image array
            if self.image_flag is None:
                image_np = np.random.randint(0, 255, (self.batch_size, 224, 224, 3), dtype=np.uint8).astype(np.float32)
            else:
                if self.batch_size != 1:
                    print('Batch size >= 1 is not supported')
                    exit()
                else:
                    image_np = img_to_array(self.image_flag).astype(np.float32)
                    image_np.resize((1, 224, 224, 3))
                    # cv2.imshow('',img)
                    # cv2.waitKey(0)
                    # cv2.destroyAllWindows()
                    # print('image type: real')

            # create request
            request = predict_pb2.PredictRequest()
            request.model_spec.name = 'resnet50-v15-fp32'
            request.model_spec.signature_name = 'serving_default'
            request.inputs['input'].CopyFrom(tf.make_tensor_proto(image_np, shape=[self.batch_size, 224, 224, 3]))
            self.request_signatures.append(request)
        return None

    async def __connection__(self, task_idx, loop_num):
        request_signatures = self.request_signatures[task_idx]
        response_list = []

        # create channel
        if self.root_cert == None:
            async with grpc.aio.insecure_channel(self.url) as channel:
                stub = prediction_service_pb2_grpc.PredictionServiceStub(channel)
                if loop_num != 0:
                    format_string = 'query: {} channel, task {}, batch {}, loop_idx {}, latency(ms) {:.1f}, tps: {:.1f}'
                    for loop_idx in range(loop_num):
                        start_time = time.time()
                        response = await stub.Predict(request_signatures)
                        stop_time = time.time()
                        latency = stop_time - start_time
                        tps = self.batch_size / latency
                        response_list.append([response, latency])
                        print(format_string.format('insecure', task_idx, self.batch_size, loop_idx, 1000*latency, tps))
                else:
                    format_string = 'query: {} channel, task {}, batch {}, latency(ms) {:.1f}, tps: {:.1f}'
                    while True:
                        start_time = time.time()
                        response = await stub.Predict(request_signatures)
                        stop_time = time.time()
                        latency = stop_time - start_time
                        tps = self.batch_size / latency
                        print(format_string.format('insecure', task_idx, self.batch_size, 1000*latency, tps))
        else:
            creds = grpc.ssl_channel_credentials(root_certificates =self.root_cert,
                                                 private_key=self.private_key,
                                                 certificate_chain=self.certificate_chain)
            async with grpc.aio.secure_channel(self.url, creds) as channel:
                stub = prediction_service_pb2_grpc.PredictionServiceStub(channel)
                if loop_num != 0:
                    format_string = 'query: {} channel, task {}, batch {}, loop_idx {}, latency(ms) {:.1f}, tps: {:.1f}'
                    for loop_idx in range(loop_num):
                        start_time = time.time()
                        response = await stub.Predict(request_signatures)
                        stop_time = time.time()
                        latency = stop_time - start_time
                        tps = self.batch_size / latency
                        response_list.append([response, latency])
                        print(format_string.format('secure', task_idx, self.batch_size, loop_idx, 1000*latency, tps))
                else:
                    format_string = 'query: {} channel, task {}, batch {}, latency(ms) {:.1f}, tps: {:.1f}'
                    while True:
                        start_time = time.time()
                        response = await stub.Predict(request_signatures)
                        stop_time = time.time()
                        latency = stop_time - start_time
                        tps = self.batch_size / latency
                        try:
                            proto_msg_to_dict(response)
                        except Exception as e:
                            print('Error response:', e)
                        print(format_string.format('secure', task_idx, self.batch_size, 1000*latency, tps))
        return response_list

    def run(self, loop_num):
        start_time = time.time()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        connections = []
        for idx in range(self.concurrent_num):
            connections.append(asyncio.ensure_future(self.__connection__(idx, loop_num)))

        loop.run_until_complete(asyncio.wait(connections))
        loop.close()

        stop_time = time.time()

        response_list = [connections[idx].result() for idx in range(self.concurrent_num)]
        print(proto_msg_to_dict(response_list[0][0][0]))

        request_time = 0
        for c_idx in range(self.concurrent_num):
            if loop_num != 0:
                for l_idx in range(loop_num):
                    request_time += response_list[c_idx][l_idx][1]

        if loop_num != 0:
            e2e_time = stop_time - start_time
            request_num = self.concurrent_num * loop_num
            latency = request_time / request_num
            tps = request_num * self.batch_size / e2e_time
            # Display the summary information
            format_string = 'summary: cnum {}, batch {}, e2e time(s) {}, average latency(ms) {}, tps: {}'
            print(format_string.format(self.concurrent_num, self.batch_size, e2e_time, 1000*latency, tps))

def main():
    benchmark_app = benchmark_engine(args.url, args.img, args.ca, args.key, args.crt, args.batch, args.cnum)
    if args.loop != 0:
        # warm up
        benchmark_app.run(5)
    # start loop
    benchmark_app.run(args.loop)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-url', default='localhost:8500', type=str, help='gRPC API Serving URL: IP:8500')
    parser.add_argument('-img', default=None, type=str, help='Image path')
    parser.add_argument('-ca', default=None, type=str, help='SSL CA file path')
    parser.add_argument('-key', default=None, type=str, help='SSL key file path')
    parser.add_argument('-crt', default=None, type=str, help='SSL certificate file path')
    parser.add_argument('-batch', default=1, type=int, help='Batch size')
    parser.add_argument('-cnum', default=16, type=int, help='Concurrent connection num')
    parser.add_argument('-loop', default=200, type=int, help='Requests loop num: 0 (infinite loop)')

    args = parser.parse_args()

    main()
