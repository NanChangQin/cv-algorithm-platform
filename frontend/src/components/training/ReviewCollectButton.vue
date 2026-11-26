<script setup lang="ts">
import { ref } from 'vue'
import { reviewsApi } from '@/api/reviews'
import { sessionRole } from '@/api/auth'
import { errorMessage } from '@/utils/errors'
const props=defineProps<{taskId:string;evaluationId?:string}>()
const busy=ref(false),id=ref(''),error=ref('')
async function collect(){busy.value=true;error.value='';try{id.value=(await reviewsApi.collect(props.taskId,props.evaluationId)).id}catch(e){error.value=errorMessage(e,'收集失败')}finally{busy.value=false}}
</script>
<template><span v-if="['admin','developer'].includes(sessionRole.get()??'')"><button v-if="!id" class="secondary-button compact-button" :disabled="busy" @click="collect">{{ busy?'收集中…':'收集并审核' }}</button><RouterLink v-else class="text-button" :to="{path:'/training/reviews',query:{review:id}}">已收集，打开审核 →</RouterLink><span v-if="error" class="alert" role="alert">{{ error }}</span></span></template>
