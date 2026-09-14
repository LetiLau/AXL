#pragma once
//using definition to have the output log both in android app or computer application
#ifdef __ANDROID__
    #include <android/log.h>
    #define LOGI(...) __android_log_print(ANDROID_LOG_INFO, "AXL_CORE", __VA_ARGS__)
    #define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, "AXL_CORE", __VA_ARGS__)
#else
    #include <cstdio>
    #define LOGI(...) do { fprintf(stdout, "[INFO] " __VA_ARGS__); fprintf(stdout, "\n"); } while(0)
    #define LOGE(...) do { fprintf(stderr, "[ERROR] " __VA_ARGS__); fprintf(stderr, "\n"); } while(0)
#endif