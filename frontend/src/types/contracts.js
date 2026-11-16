/** @typedef {{id:number,code:string,amount:number,distance:number,holiday:boolean,label:0|1|null,note:string}} Claim */
/** @typedef {{id:number,name:string,threshold:number,state:string,result:object,is_active:boolean}} Training */
/** @typedef {{id:number,claim_id:number,training_id:number,score:number,decision:string,status:string,superseded_by:number|null}} Review */
/** @typedef {{id:number,baseline_id:number,candidate_id:number,state:string,reject_reason:string,error:string,result:object}} Comparison */
